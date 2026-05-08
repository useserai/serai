import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from job_source_router import fetch_jobs_for_company, fetch_job_detail_for_company
from normalize import (
    normalize_job,
    extract_comp_from_greenhouse_detail,
    fill_missing_comp,
    format_compensation,
)
from job_filter import fast_filter_title_geo, check_comp
from company_registry import active_company_keys
from search_backends import search_web

load_dotenv()

DISCOVERED_COMPANIES_FILE = Path("discovered_companies.json")

DISCOVERY_RECHECK_DAYS = 14
DISCOVERY_RESULT_COUNT = 20
DEFAULT_MAX_DISCOVERY_QUERIES = 100  # default cap on total Brave queries per run; override via config.yaml's discovery.max_queries

DISCOVERY_DOMAINS = [
    {
        "source": "greenhouse",
        "domain": "job-boards.greenhouse.io",
        "mode": "board",
    },
    {
        "source": "ashby",
        "domain": "jobs.ashbyhq.com",
        "mode": "board",
    },
    {
        "source": "lever",
        "domain": "jobs.lever.co",
        "mode": "board",
    },
    {
        "source": "workable",
        "domain": "apply.workable.com",
        "mode": "board",
    },
    {
        "source": "smartrecruiters",
        "domain": "jobs.smartrecruiters.com",
        "mode": "board",
    },
    {
        "source": "workday",
        "domain": "myworkdayjobs.com",
        "mode": "url_only",
    },
]


def load_discovered_companies() -> dict:
    if not DISCOVERED_COMPANIES_FILE.exists():
        return {}

    try:
        return json.loads(DISCOVERED_COMPANIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_discovered_companies(data: dict) -> None:
    DISCOVERED_COMPANIES_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_search_queries(
    patterns: list,
    broad_sweep_titles: list,
    region_search_terms: list = None,
) -> list:
    """Build Brave queries. When region_search_terms is non-empty, generate region-augmented
    variants alongside the originals so non-US users get region-relevant results surfaced
    above the US-default Brave ranking. Total queries capped at MAX_DISCOVERY_QUERIES."""
    if region_search_terms is None:
        region_search_terms = []

    queries = []

    def _emit(query_text, source, mode, query_mode, seed_title):
        queries.append(
            {
                "query": query_text,
                "source": source,
                "domain_mode": mode,
                "query_mode": query_mode,
                "seed_title": seed_title,
            }
        )

    def _emit_with_region_variants(base_query, source, mode, query_mode, seed_title):
        # Always emit the original (US-friendly remote roles still surface).
        _emit(base_query, source, mode, query_mode, seed_title)
        # Then emit one variant per region term.
        for region in region_search_terms:
            region = (region or "").strip()
            if not region:
                continue
            _emit(f'{base_query} {region}', source, mode, query_mode, seed_title)

    for pattern in patterns:
        for title in pattern.get("titles", []):
            clean_title = (title or "").strip()
            if not clean_title:
                continue

            for domain_cfg in DISCOVERY_DOMAINS:
                base = f'site:{domain_cfg["domain"]} "{clean_title}"'
                _emit_with_region_variants(
                    base, domain_cfg["source"], domain_cfg["mode"], "precise", clean_title
                )

    for title in broad_sweep_titles:
        for domain_cfg in DISCOVERY_DOMAINS:
            base = f'site:{domain_cfg["domain"]} "{title}"'
            _emit_with_region_variants(
                base, domain_cfg["source"], domain_cfg["mode"], "broad", title
            )

    seen = set()
    deduped = []

    for item in queries:
        key = item["query"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    # Cap is config-driven: read filters.geo.discovery.max_queries from config.yaml,
    # defaulting to DEFAULT_MAX_DISCOVERY_QUERIES if unset.
    try:
        import config_loader
        max_queries = config_loader.get_max_discovery_queries()
    except Exception:
        max_queries = DEFAULT_MAX_DISCOVERY_QUERIES

    if len(deduped) > max_queries:
        print(
            f"[discovery] capping queries from {len(deduped)} to {max_queries} "
            f"(discovery.max_queries) to protect Brave credits"
        )
        deduped = deduped[:max_queries]
    else:
        print(f"[discovery] {len(deduped)} queries to run (cap: {max_queries})")

    return deduped


def extract_board_candidate(result: dict) -> Optional[dict]:
    """
    Used only for true board-based ATS sources like Greenhouse and Ashby.
    Workday is intentionally handled separately as URL-only discovery.
    """
    url = result["url"]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]

    if "greenhouse.io" in host:
        if host.startswith("job-boards.greenhouse.io") or host.startswith("boards.greenhouse.io"):
            if not parts:
                return None
            token = parts[0]
            return {
                "source": "greenhouse",
                "board_token": token,
                "company_slug": token.lower(),
                "board_url": f"https://job-boards.greenhouse.io/{token}",
                "example_search_url": url,
            }

    if "ashbyhq.com" in host and host.startswith("jobs.ashbyhq.com"):
        if not parts:
            return None
        token = parts[0]
        return {
            "source": "ashby",
            "board_token": token,
            "company_slug": token.lower(),
            "board_url": f"https://jobs.ashbyhq.com/{token}",
            "example_search_url": url,
        }

    if "lever.co" in host and host.startswith("jobs.lever.co"):
        if not parts:
            return None
        token = parts[0]
        return {
            "source": "lever",
            "board_token": token,
            "company_slug": token.lower(),
            "board_url": f"https://jobs.lever.co/{token}",
            "example_search_url": url,
        }

    if "workable.com" in host and host.startswith("apply.workable.com"):
        if not parts:
            return None
        token = parts[0]
        return {
            "source": "workable",
            "board_token": token,
            "company_slug": token.lower(),
            "board_url": f"https://apply.workable.com/{token}",
            "example_search_url": url,
        }

    if "smartrecruiters.com" in host and host.startswith("jobs.smartrecruiters.com"):
        if not parts:
            return None
        # SmartRecruiters API is case-sensitive on the company slug — preserve original case in board_token.
        token = parts[0]
        return {
            "source": "smartrecruiters",
            "board_token": token,
            "company_slug": token.lower(),
            "board_url": f"https://jobs.smartrecruiters.com/{token}",
            "example_search_url": url,
        }

    return None


def extract_workday_candidate(result: dict) -> Optional[dict]:
    """
    Workday discovery is URL-only.
    We do not crawl the whole Workday board.
    Each public job URL is treated as a single discovered signal.
    """
    url = result["url"]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]

    if "myworkdayjobs.com" not in host:
        return None

    locale_pattern = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")

    if not parts:
        return None

    workday_locale = ""
    workday_site = ""
    job_slug = ""
    source_job_id = ""

    # /en-US/Careers/job/Senior-Contracts-Manager_R0014076-1
    if locale_pattern.match(parts[0]) and len(parts) >= 4 and parts[2].lower() == "job":
        workday_locale = parts[0]
        workday_site = parts[1]
        job_slug = parts[3]

    # /Careers/job/Senior-Contracts-Manager_R0014076-1
    elif len(parts) >= 3 and parts[1].lower() == "job":
        workday_site = parts[0]
        job_slug = parts[2]

    else:
        return None

    if not workday_site or workday_site.lower() in {"job", "jobs"}:
        return None

    if "_" in job_slug:
        source_job_id = job_slug.split("_")[-1]
    else:
        source_job_id = job_slug

    if not source_job_id:
        source_job_id = job_slug

    company_slug = host.split(".")[0].lower()

    candidate = {
        "source": "workday",
        "company_slug": company_slug,
        "workday_host": host,
        "workday_site": workday_site,
        "board_url": (
            f"https://{host}/{workday_locale}/{workday_site}"
            if workday_locale
            else f"https://{host}/{workday_site}"
        ),
        "example_search_url": url,
        "first_matching_url": url,
        "search_result_title": result.get("title", ""),
        "search_result_description": result.get("description", ""),
        "source_job_id": source_job_id,
        "job_slug": job_slug,
    }

    if workday_locale:
        candidate["workday_locale"] = workday_locale

    return candidate


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def should_recheck_discovered(existing_record: Optional[dict]) -> bool:
    if not existing_record:
        return True

    last_seen = (
        existing_record.get("last_checked_at")
        or existing_record.get("updated_at")
        or existing_record.get("discovered_at")
    )
    last_dt = parse_iso_datetime(last_seen)

    if not last_dt:
        return True

    age = datetime.now(timezone.utc) - last_dt
    return age >= timedelta(days=DISCOVERY_RECHECK_DAYS)


def candidate_registry_key(candidate: dict) -> tuple:
    if candidate["source"] == "workday":
        return (
            candidate["source"],
            candidate["workday_host"],
            candidate.get("workday_locale", ""),
            candidate["workday_site"],
            candidate.get("source_job_id", ""),
        )
    return (candidate["source"], candidate["board_token"])


def candidate_store_key(candidate: dict) -> str:
    if candidate["source"] == "workday":
        return (
            f"{candidate['source']}::"
            f"{candidate['workday_host']}::"
            f"{candidate.get('workday_locale', '')}::"
            f"{candidate['workday_site']}::"
            f"{candidate.get('source_job_id', '')}"
        )
    return f"{candidate['source']}::{candidate['board_token']}"


def workday_active_key(candidate: dict) -> tuple:
    return (
        candidate["source"],
        candidate["workday_host"],
        candidate.get("workday_locale", ""),
        candidate["workday_site"],
    )


def collect_board_candidates(patterns: list, broad_sweep_titles: list) -> list:
    import config_loader
    region_search_terms = config_loader.get_filter_config().get("home_region", {}).get("search_terms", [])
    query_specs = build_search_queries(patterns, broad_sweep_titles, region_search_terms)
    candidates = []
    seen = set()

    discovered_store = load_discovered_companies()
    active_keys = active_company_keys()

    for query_spec in query_specs:
        query = query_spec["query"]

        try:
            results = search_web(query, count=DISCOVERY_RESULT_COUNT)
        except Exception as e:
            print(f"[discovery] search failed for query={query!r}: {e}")
            continue

        for result in results:
            source = query_spec["source"]
            domain_mode = query_spec["domain_mode"]

            if source == "workday" and domain_mode == "url_only":
                candidate = extract_workday_candidate(result)
            else:
                candidate = extract_board_candidate(result)

            if not candidate:
                continue

            key = candidate_registry_key(candidate)
            if key in seen:
                continue

            if candidate["source"] == "workday":
                if workday_active_key(candidate) in active_keys:
                    continue
            else:
                if (candidate["source"], candidate["board_token"]) in active_keys:
                    continue

            store_key = candidate_store_key(candidate)
            existing_record = discovered_store.get(store_key)

            if existing_record and not should_recheck_discovered(existing_record):
                print(
                    f"[discovery] skipping recently checked candidate: "
                    f"{candidate['company_slug']} ({candidate['source']})"
                )
                continue

            seen.add(key)
            candidate["discovery_query"] = query
            candidate["discovery_mode"] = query_spec["query_mode"]
            candidate["seed_title"] = query_spec["seed_title"]
            candidates.append(candidate)

    print(f"[discovery] collected {len(candidates)} board candidates")
    return candidates


def score_candidate_job(job: dict, filter_result: dict) -> float:
    score = float(filter_result.get("title_score", 0))
    if job.get("comp_min") is not None or job.get("comp_max") is not None:
        score += 1.0
    if job.get("location"):
        score += 0.5
    return score


def looks_adjacent(title: str, title_score: float, adjacent_title_keywords: list) -> bool:
    lowered = (title or "").lower()

    if title_score >= 5:
        return True

    for keyword in adjacent_title_keywords:
        if keyword in lowered:
            return True

    return False


def build_company_config(candidate: dict) -> dict:
    if candidate["source"] == "workday":
        config = {
            "company_slug": candidate["company_slug"],
            "source": "workday",
            "enabled": True,
            "workday_host": candidate["workday_host"],
            "workday_site": candidate["workday_site"],
        }
        if candidate.get("workday_locale"):
            config["workday_locale"] = candidate["workday_locale"]
        return config

    return {
        "company_slug": candidate["company_slug"],
        "source": candidate["source"],
        "board_token": candidate["board_token"],
        "enabled": True,
    }


def infer_workday_title_from_url(candidate: dict) -> str:
    job_slug = candidate.get("job_slug", "") or ""
    if not job_slug:
        return ""

    title_part = job_slug
    if "_" in job_slug:
        title_part = job_slug.rsplit("_", 1)[0]

    title_part = title_part.replace("-", " ").replace("_", " ").strip()
    title_part = re.sub(r"\s+", " ", title_part)
    return title_part


def build_workday_job_from_candidate(candidate: dict) -> dict:
    inferred_title = infer_workday_title_from_url(candidate)

    return {
        "source": "workday",
        "title": inferred_title or candidate.get("search_result_title", ""),
        "location": "",
        "url": candidate.get("first_matching_url") or candidate.get("example_search_url", ""),
        "source_job_id": candidate.get("source_job_id", ""),
        "description": candidate.get("search_result_description", ""),
        "job_description": candidate.get("search_result_description", ""),
        "comp_min": None,
        "comp_max": None,
        "compensation": "",
    }


def gather_company_evidence(
    candidate: dict,
    adjacent_title_keywords: list,
    max_direct: int = 3,
    max_adjacent: int = 5,
) -> Optional[dict]:
    if candidate["source"] == "workday":
        job = build_workday_job_from_candidate(candidate)
        filter_result = fast_filter_title_geo(job)
        title_score = float(filter_result.get("title_score", 0))

        direct_matches = []
        adjacent_matches = []

        if filter_result.get("passed"):
            job["discovery_match_score"] = score_candidate_job(job, filter_result)
            direct_matches.append(job)
        elif looks_adjacent(job.get("title", ""), title_score, adjacent_title_keywords):
            job["discovery_match_score"] = title_score
            adjacent_matches.append(job)

        if not direct_matches and not adjacent_matches:
            return {
                "total_jobs": 1,
                "direct_match_count": 0,
                "adjacent_match_count": 0,
                "direct_matches": [],
                "adjacent_matches": [],
                "example_titles": [job.get("title", "")] if job.get("title") else [],
                "has_relevant_hiring_signal": False,
            }

        return {
            "total_jobs": 1,
            "direct_match_count": len(direct_matches),
            "adjacent_match_count": len(adjacent_matches),
            "direct_matches": direct_matches[:max_direct],
            "adjacent_matches": adjacent_matches[:max_adjacent],
            "example_titles": [job.get("title", "")] if job.get("title") else [],
            "has_relevant_hiring_signal": True,
        }

    company_config = build_company_config(candidate)

    try:
        raw_jobs = fetch_jobs_for_company(company_config)
    except Exception as e:
        print(
            f"[discovery] fetch failed for {candidate['source']}:{candidate.get('board_token', candidate.get('company_slug'))}: {e}"
        )
        return None

    direct_matches = []
    adjacent_matches = []
    example_titles = []
    total_jobs = len(raw_jobs)

    for raw_job in raw_jobs:
        try:
            job = normalize_job(raw_job, company_config)

            filter_result = fast_filter_title_geo(job)
            title_score = float(filter_result.get("title_score", 0))
            is_passed = bool(filter_result.get("passed"))
            is_adjacent = looks_adjacent(job.get("title", ""), title_score, adjacent_title_keywords)
            job_detail = None

            # Fetch per-job details only for candidates we'll evaluate downstream
            # (direct or adjacent). Big boards burned ~5 minutes on detail fetches for
            # jobs we discard immediately. Greenhouse needs detail for comp; Workable
            # needs detail for full description (widget API is metadata-only).
            if (is_passed or is_adjacent) and company_config["source"] == "greenhouse":
                try:
                    job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                    comp_min, comp_max = extract_comp_from_greenhouse_detail(job_detail)
                    if comp_min is not None or comp_max is not None:
                        job["comp_min"] = comp_min
                        job["comp_max"] = comp_max
                except Exception:
                    job_detail = None

            elif (is_passed or is_adjacent) and company_config["source"] == "workable":
                try:
                    from normalize import extract_workable_detail_fields
                    job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                    detail_fields = extract_workable_detail_fields(job_detail)
                    if detail_fields["description"]:
                        job["description"] = detail_fields["description"]
                        job["job_description"] = detail_fields["description"]
                    if detail_fields["comp_min"] is not None or detail_fields["comp_max"] is not None:
                        job["comp_min"] = detail_fields["comp_min"]
                        job["comp_max"] = detail_fields["comp_max"]
                except Exception:
                    job_detail = None

            elif (is_passed or is_adjacent) and company_config["source"] == "smartrecruiters":
                try:
                    from normalize import extract_smartrecruiters_detail_fields
                    job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                    detail_fields = extract_smartrecruiters_detail_fields(job_detail)
                    if detail_fields["description"]:
                        job["description"] = detail_fields["description"]
                        job["job_description"] = detail_fields["description"]
                    if detail_fields["comp_min"] is not None or detail_fields["comp_max"] is not None:
                        job["comp_min"] = detail_fields["comp_min"]
                        job["comp_max"] = detail_fields["comp_max"]
                    if detail_fields["url"] and not job.get("url"):
                        job["url"] = detail_fields["url"]
                except Exception:
                    job_detail = None

            job = fill_missing_comp(job, job_detail=job_detail)
            job["compensation"] = format_compensation(job.get("comp_min"), job.get("comp_max"))

            comp_result = check_comp(job.get("comp_min"), job.get("comp_max"))

            if len(example_titles) < 8:
                example_titles.append(job.get("title", ""))

            if is_passed and comp_result.get("passed"):
                job["discovery_match_score"] = score_candidate_job(job, filter_result)
                direct_matches.append(job)
                continue

            if is_adjacent:
                job["discovery_match_score"] = title_score
                adjacent_matches.append(job)

        except Exception as e:
            print(
                f"[discovery] job normalization/filter failure for "
                f"{candidate['source']}:{candidate.get('board_token', candidate.get('company_slug'))}: {e}"
            )
            continue

    direct_matches.sort(key=lambda j: j.get("discovery_match_score", 0), reverse=True)
    adjacent_matches.sort(key=lambda j: j.get("discovery_match_score", 0), reverse=True)

    return {
        "total_jobs": total_jobs,
        "direct_match_count": len(direct_matches),
        "adjacent_match_count": len(adjacent_matches),
        "direct_matches": direct_matches[:max_direct],
        "adjacent_matches": adjacent_matches[:max_adjacent],
        "example_titles": [title for title in example_titles if title][:8],
        "has_relevant_hiring_signal": (len(direct_matches) > 0 or len(adjacent_matches) > 0),
    }


def discover_companies(
    patterns: list,
    broad_sweep_titles: list,
    adjacent_title_keywords: list,
):
    """Yield each verified candidate as soon as its evidence is gathered.

    Generator so the caller can run LLM scoring + Notion writes per candidate
    without waiting for the full discovery sweep. Time-to-first-Notion-write
    drops from "end of run" (possibly hours) to "minutes after the first
    candidate verifies." Crashes mid-sweep preserve every candidate already
    yielded and persisted upstream.
    """
    candidates = collect_board_candidates(patterns, broad_sweep_titles)
    yielded = 0

    for candidate in candidates:
        evidence = gather_company_evidence(candidate, adjacent_title_keywords)
        if not evidence:
            continue

        yielded += 1
        yield {
            **candidate,
            **evidence,
            "first_matching_title": (
                evidence["direct_matches"][0].get("title")
                if evidence["direct_matches"]
                else (evidence["adjacent_matches"][0].get("title") if evidence["adjacent_matches"] else "")
            ),
            "first_matching_url": (
                evidence["direct_matches"][0].get("url")
                if evidence["direct_matches"]
                else (evidence["adjacent_matches"][0].get("url") if evidence["adjacent_matches"] else "")
            ),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    print(f"[discovery] streamed {yielded} candidates with evidence")


def persist_discovery_results(discovered: list) -> None:
    existing = load_discovered_companies()
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in discovered:
        if item["source"] == "workday":
            key = (
                f"{item['source']}::"
                f"{item.get('workday_host', '')}::"
                f"{item.get('workday_locale', '')}::"
                f"{item.get('workday_site', '')}::"
                f"{item.get('source_job_id', '')}"
            )
        else:
            key = f"{item['source']}::{item.get('board_token', '')}"

        existing[key] = {
            "company_slug": item["company_slug"],
            "source": item["source"],
            "board_url": item.get("board_url"),
            "example_search_url": item.get("example_search_url"),
            "discovery_query": item.get("discovery_query"),
            "discovery_mode": item.get("discovery_mode"),
            "seed_title": item.get("seed_title"),
            "first_matching_title": item.get("first_matching_title"),
            "first_matching_url": item.get("first_matching_url"),
            "total_jobs": item.get("total_jobs", 0),
            "direct_match_count": item.get("direct_match_count", 0),
            "adjacent_match_count": item.get("adjacent_match_count", 0),
            "example_titles": item.get("example_titles", []),
            "matching_jobs": [
                {
                    "title": job.get("title"),
                    "location": job.get("location"),
                    "url": job.get("url"),
                    "comp_min": job.get("comp_min"),
                    "comp_max": job.get("comp_max"),
                    "compensation": job.get("compensation"),
                }
                for job in item.get("direct_matches", [])
            ],
            "adjacent_jobs": [
                {
                    "title": job.get("title"),
                    "location": job.get("location"),
                    "url": job.get("url"),
                    "comp_min": job.get("comp_min"),
                    "comp_max": job.get("comp_max"),
                    "compensation": job.get("compensation"),
                }
                for job in item.get("adjacent_matches", [])
            ],
            "discovered_at": item.get("discovered_at"),
            "last_checked_at": now_iso,
            "status": item.get("status", existing.get(key, {}).get("status", "pending_company_review")),
        }

        if item["source"] == "workday":
            existing[key]["workday_host"] = item.get("workday_host")
            existing[key]["workday_locale"] = item.get("workday_locale", "")
            existing[key]["workday_site"] = item.get("workday_site")
            existing[key]["source_job_id"] = item.get("source_job_id", "")
            existing[key]["job_slug"] = item.get("job_slug", "")
            existing[key]["search_result_title"] = item.get("search_result_title", "")
            existing[key]["search_result_description"] = item.get("search_result_description", "")
        else:
            existing[key]["board_token"] = item.get("board_token")

    save_discovered_companies(existing)