import csv
from datetime import datetime, timezone
from pathlib import Path

from company_registry import upsert_active_company, COMPANY_REGISTRY
from company_discovery import (
    discover_companies,
    persist_discovery_results,
    load_discovered_companies,
    save_discovered_companies,
)
from discovery_patterns import (
    DISCOVERY_PATTERNS,
    DEFAULT_BROAD_SWEEP_TITLES,
    DEFAULT_ADJACENT_TITLE_KEYWORDS,
    load_discovery_keywords
)
from config_loader import is_new_config, get_discovery_config, get_candidate_prompt
from role_title_gates import (
    load_unknown_bucket_title_substrings,
    role_title_matches_exclusion_substrings,
)
from llm_company_score import llm_score_company
from run_metrics import append_run_metric
from notion_helper import upsert_eval_job
from llm_score import llm_score_job
from job_filter import fast_filter_title_geo, check_comp
from normalize import normalize_yc_job
from sources.yc_jobs import get_yc_jobs

OUTPUT_CSV = Path("discovered_company_results.csv")
YC_OUTPUT_CSV = Path("yc_discovered_job_results.csv")
CANDIDATE_PROFILE_PATH = Path("candidate_data/candidate_profile.txt")
UNKNOWN_BUCKET_TITLE_SUBSTRINGS = load_unknown_bucket_title_substrings(
    CANDIDATE_PROFILE_PATH
)
PROMOTION_THRESHOLD = 7.0
WATCHLIST_THRESHOLD = 6.5
ROLE_INTEREST_THRESHOLD = 7.0


def load_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[discovery] failed to load candidate profile: {e}")
        return ""


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def role_fit_score(role: dict) -> float:
    """
    Role-fit score intentionally excludes company attractiveness.
    Unknown-bucket roles must earn their way through on job-fit evidence.
    """
    return round(
        to_float(role.get("strength_overlap")) * 0.40
        + to_float(role.get("role_interest")) * 0.25
        + to_float(role.get("level_fit")) * 0.20
        + to_float(role.get("job_confidence")) * 0.10
        + to_float(role.get("title_score")) * 0.05,
        2,
    )


def compute_apply_score(role: dict) -> float:
    """
    Overall ranking score, but with company attractiveness de-emphasized.
    """
    return round(
        to_float(role.get("strength_overlap")) * 0.35
        + to_float(role.get("role_interest")) * 0.25
        + to_float(role.get("level_fit")) * 0.15
        + to_float(role.get("job_confidence")) * 0.10
        + to_float(role.get("overall_interest_score")) * 0.05
        + to_float(role.get("title_score")) * 0.05
        + to_float(role.get("company_interest_score")) * 0.05,
        2,
    )


def is_apply_worthy(role: dict) -> bool:
    title_bucket = role.get("title_bucket", "unknown")

    if title_bucket == "core_pm":
        return (
            to_float(role.get("strength_overlap")) >= 8
            and to_float(role.get("role_interest")) >= 7
            and to_float(role.get("level_fit")) >= 7
            and to_float(role.get("overall_interest_score")) >= 7
            and compute_apply_score(role) >= 7.5
        )

    if title_bucket == "adjacent":
        return (
            to_float(role.get("strength_overlap")) >= 8
            and to_float(role.get("role_interest")) >= 7
            and to_float(role.get("level_fit")) >= 7
            and to_float(role.get("job_confidence")) >= 7
            and role_fit_score(role) >= 7.6
            and compute_apply_score(role) >= 7.4
        )

    # unknown title bucket: need extraordinary role-fit evidence
    return (
        not role_title_matches_exclusion_substrings(role, UNKNOWN_BUCKET_TITLE_SUBSTRINGS)
        and to_float(role.get("strength_overlap")) >= 8
        and to_float(role.get("role_interest")) >= 8
        and to_float(role.get("level_fit")) >= 8
        and to_float(role.get("job_confidence")) >= 8
        and role_fit_score(role) >= 8.0
        and compute_apply_score(role) >= 7.8
    )


def is_network_worthy(role: dict) -> bool:
    title_bucket = role.get("title_bucket", "unknown")

    if title_bucket == "core_pm":
        return (
            to_float(role.get("overall_interest_score")) >= 7
            and compute_apply_score(role) >= 7.0
            and (
                to_float(role.get("strength_overlap")) >= 7
                or to_float(role.get("role_interest")) >= 8
                or to_float(role.get("level_fit")) >= 8
            )
        )

    if title_bucket == "adjacent":
        return (
            to_float(role.get("overall_interest_score")) >= 6.8
            and to_float(role.get("job_confidence")) >= 6.5
            and role_fit_score(role) >= 7.0
            and (
                to_float(role.get("strength_overlap")) >= 7
                or to_float(role.get("role_interest")) >= 7.5
                or to_float(role.get("level_fit")) >= 7.5
            )
        )

    # unknown title bucket: must survive on role fit alone, not company fit
    if role_title_matches_exclusion_substrings(role, UNKNOWN_BUCKET_TITLE_SUBSTRINGS):
        return False

    return (
        to_float(role.get("job_confidence")) >= 7
        and to_float(role.get("strength_overlap")) >= 8
        and to_float(role.get("role_interest")) >= 7.5
        and to_float(role.get("level_fit")) >= 7.5
        and role_fit_score(role) >= 7.6
    )


def assign_final_routes(scored_roles: list) -> list:
    if not scored_roles:
        return scored_roles

    for role in scored_roles:
        role["role_fit_score"] = role_fit_score(role)
        role["apply_score"] = compute_apply_score(role)

    ranked = sorted(
        scored_roles,
        key=lambda r: (
            to_float(r.get("apply_score")),
            to_float(r.get("role_fit_score")),
            to_float(r.get("strength_overlap")),
            to_float(r.get("role_interest")),
            to_float(r.get("level_fit")),
        ),
        reverse=True,
    )

    for idx, role in enumerate(ranked, start=1):
        role["company_rank"] = idx

    top_role = ranked[0]

    if is_apply_worthy(top_role):
        top_role["final_route"] = "Apply"
    elif is_network_worthy(top_role):
        top_role["final_route"] = "Network"
    else:
        top_role["final_route"] = "Skip"

    for role in ranked[1:]:
        prelim = role.get("preliminary_route", "Skip")

        if is_network_worthy(role) and prelim in {"Apply", "Network", "Review"}:
            role["final_route"] = "Network"
        elif prelim == "Review":
            role["final_route"] = "Review"
        else:
            role["final_route"] = "Skip"

    return ranked


def build_yc_output_row(job: dict, company_result: dict, llm_result: dict) -> dict:
    return {
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "url": job.get("url", ""),
        "source": job.get("source", ""),
        "source_job_id": job.get("source_job_id", ""),
        "comp_min": job.get("comp_min", ""),
        "comp_max": job.get("comp_max", ""),
        "compensation": job.get("compensation", ""),
        "company_interest_score": company_result.get("company_interest_score", ""),
        "role_interest": llm_result.get("role_interest", ""),
        "overall_interest_score": llm_result.get("overall_interest_score", ""),
        "strength_overlap": llm_result.get("strength_overlap", ""),
        "level_fit": llm_result.get("level_fit", ""),
        "job_confidence": llm_result.get("job_confidence", ""),
        "title_score": llm_result.get("title_score", ""),
        "title_bucket": llm_result.get("title_bucket", ""),
        "preliminary_route": llm_result.get("preliminary_route", ""),
        "final_route": llm_result.get("final_route", ""),
        "company_rank": llm_result.get("company_rank", ""),
        "apply_score": llm_result.get("apply_score", ""),
        "differentiation_reason": llm_result.get("differentiation_reason", ""),
        "main_reservation": llm_result.get("main_reservation", ""),
    }


def determine_company_status(item: dict, company_result: dict) -> str:
    company_score = to_float(company_result.get("company_interest_score"))
    disqualifiers = company_result.get("disqualifier_flags", []) or []
    direct_count = int(item.get("direct_match_count", 0))
    adjacent_count = int(item.get("adjacent_match_count", 0))

    if disqualifiers:
        return "rejected_disqualifier"

    strong_role_signal = direct_count > 0

    if company_score >= PROMOTION_THRESHOLD and strong_role_signal:
        return "promoted_to_active"

    if company_score >= PROMOTION_THRESHOLD:
        return "watchlist_company_only"

    if (
        company_score >= WATCHLIST_THRESHOLD
        and adjacent_count > 0
        and direct_count == 0
    ):
        return "watchlist_with_adjacent_roles"

    return "rejected_low_company_fit"


def maybe_promote_company(item: dict, company_result: dict, status: str) -> bool:
    promotable_statuses = {
        "promoted_to_active",
        "promoted_to_active_company_only",
        "promoted_to_active_with_strong_roles",
        "promoted_to_active_with_adjacent_roles",
    }

    if status not in promotable_statuses:
        return False

    company_score = to_float(company_result.get("company_interest_score"))

    try:
        if item["source"] == "workday":
            changed = upsert_active_company(
                {
                    "company_slug": item["company_slug"],
                    "source": "workday",
                    "enabled": True,
                    "workday_host": item.get("workday_host", ""),
                    "workday_site": item.get("workday_site", ""),
                    "workday_locale": item.get("workday_locale", ""),
                    "board_url": item.get("board_url"),
                    "company_name": item["company_slug"],
                    "discovered_at": item.get("discovered_at"),
                    "notes": (
                        f"Promoted by discovery loop "
                        f"(status={status}, score={company_score}, "
                        f"direct={item.get('direct_match_count', 0)}, "
                        f"adjacent={item.get('adjacent_match_count', 0)})"
                    ),
                }
            )
        else:
            changed = upsert_active_company(
                {
                    "company_slug": item["company_slug"],
                    "source": item["source"],
                    "board_token": item.get("board_token", ""),
                    "enabled": True,
                    "board_url": item.get("board_url"),
                    "company_name": item["company_slug"],
                    "discovered_at": item.get("discovered_at"),
                    "notes": (
                        f"Promoted by discovery loop "
                        f"(status={status}, score={company_score}, "
                        f"direct={item.get('direct_match_count', 0)}, "
                        f"adjacent={item.get('adjacent_match_count', 0)})"
                    ),
                }
            )

        print(
            f"[discovery] promoted {item['company_slug']} "
            f"({item['source']}) status={status} changed={changed}"
        )
        return True

    except Exception as e:
        print(
            f"[discovery] promotion failed for {item['company_slug']} "
            f"({item['source']}): {e}"
        )
        return False


def get_store_key(item: dict) -> str:
    if item["source"] == "workday":
        return (
            f"{item['source']}::"
            f"{item.get('workday_host', '')}::"
            f"{item.get('workday_locale', '')}::"
            f"{item.get('workday_site', '')}::"
            f"{item.get('source_job_id', '')}"
        )
    return f"{item['source']}::{item.get('board_token', '')}"


def process_ats_company_discovery(
    candidate_profile: str,
    metrics: dict,
    errors: list,
    broad_sweep_titles: list,
    adjacent_title_keywords: list,
    discovery_patterns: list = None,
) -> None:
    discovered = discover_companies(
        discovery_patterns or DISCOVERY_PATTERNS,
        broad_sweep_titles,
        adjacent_title_keywords,
    )
    metrics["discovery_candidates"] = len(discovered)
    metrics["companies_checked"] = len(discovered)

    if not discovered:
        print("[discovery] no candidate companies found")
        return

    rows = []
    discovered_store = load_discovered_companies()

    for item in discovered:
        company_slug = item["company_slug"]
        company_result = llm_score_company(company_slug, candidate_profile)
        company_score = to_float(company_result.get("company_interest_score"))
        status = determine_company_status(item, company_result)

        try:
            promoted = maybe_promote_company(item, company_result, status)
        except Exception as e:
            promoted = False
            errors.append(f"Promotion failed for {company_slug}: {e}")
            print(
                f"[discovery] promotion error for {company_slug} ({item['source']}): {e}"
            )

        if promoted:
            metrics["discovery_promoted"] += 1
        else:
            print(
                f"[discovery] kept pending {company_slug} ({item['source']}) "
                f"score={company_score} status={status}"
            )

        store_key = get_store_key(item)

        discovered_store[store_key] = {
            "company_slug": company_slug,
            "source": item["source"],
            "board_token": item.get("board_token"),
            "board_url": item.get("board_url"),
            "example_search_url": item.get("example_search_url"),
            "discovery_query": item.get("discovery_query"),
            "discovery_mode": item.get("discovery_mode"),
            "seed_title": item.get("seed_title"),
            "first_matching_title": item.get("first_matching_title"),
            "first_matching_url": item.get("first_matching_url"),
            "company_interest_score": company_score,
            "company_interest_reason": company_result.get("company_interest_reason"),
            "disqualifier_flags": company_result.get("disqualifier_flags", []),
            "status": status,
            "total_jobs": item.get("total_jobs", 0),
            "direct_match_count": item.get("direct_match_count", 0),
            "adjacent_match_count": item.get("adjacent_match_count", 0),
            "example_titles": item.get("example_titles", []),
            "matching_jobs": item.get("direct_matches", []),
            "adjacent_jobs": item.get("adjacent_matches", []),
            "discovered_at": item.get("discovered_at"),
        }

        if item["source"] == "workday":
            discovered_store[store_key]["workday_host"] = item.get("workday_host")
            discovered_store[store_key]["workday_locale"] = item.get("workday_locale", "")
            discovered_store[store_key]["workday_site"] = item.get("workday_site")
            discovered_store[store_key]["source_job_id"] = item.get("source_job_id", "")
            discovered_store[store_key]["job_slug"] = item.get("job_slug", "")
            discovered_store[store_key]["search_result_title"] = item.get("search_result_title", "")
            discovered_store[store_key]["search_result_description"] = item.get("search_result_description", "")

        rows.append(
            {
                "company_slug": company_slug,
                "source": item["source"],
                "board_token": item.get("board_token", ""),
                "company_interest_score": company_score,
                "status": status,
                "total_jobs": item.get("total_jobs", 0),
                "direct_match_count": item.get("direct_match_count", 0),
                "adjacent_match_count": item.get("adjacent_match_count", 0),
                "first_matching_title": item.get("first_matching_title"),
                "first_matching_url": item.get("first_matching_url"),
                "discovery_query": item.get("discovery_query"),
            }
        )

    save_discovered_companies(discovered_store)

    persist_discovery_results(
        [
            {
                **item,
                "status": discovered_store[get_store_key(item)]["status"],
            }
            for item in discovered
        ]
    )

    if rows:
        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        print(f"[discovery] wrote {len(rows)} rows to {OUTPUT_CSV}")


def process_yc_job_discovery(candidate_profile: str, metrics: dict, errors: list) -> None:
    raw_yc_jobs = get_yc_jobs()
    metrics["jobs_fetched"] += len(raw_yc_jobs)

    if not raw_yc_jobs:
        print("[yc_jobs] no YC jobs fetched")
        return

    company_scores = {}
    roles_by_company = {}

    for raw_job in raw_yc_jobs:
        try:
            job = normalize_yc_job(raw_job)

            filter_result = fast_filter_title_geo(job)
            if not filter_result["passed"]:
                continue

            comp_result = check_comp(job.get("comp_min"), job.get("comp_max"))
            if not comp_result["passed"]:
                continue

            metrics["jobs_evaluated"] += 1

            company_slug = job["company"]
            if company_slug not in company_scores:
                company_scores[company_slug] = llm_score_company(company_slug, candidate_profile)

            company_result = company_scores[company_slug]
            llm_result = llm_score_job(job, candidate_profile, company_result)

            combined = {
                **job,
                **llm_result,
                "title_score": filter_result.get("title_score"),
                "title_bucket": filter_result.get("title_bucket"),
            }

            roles_by_company.setdefault(company_slug, []).append(combined)

        except Exception as e:
            msg = f"[yc_jobs] failed to evaluate {raw_job.get('job_url', 'unknown')}: {e}"
            print(msg)
            errors.append(msg)

    yc_rows = []

    for company_slug, roles in roles_by_company.items():
        company_result = company_scores[company_slug]
        ranked_roles = assign_final_routes(roles)

        company_disqualifiers = company_result.get("disqualifier_flags", []) or []
        if company_disqualifiers:
            for role in ranked_roles:
                role["final_route"] = "Skip"
                role["main_reservation"] = (
                    f"Candidate disqualifier: {', '.join(company_disqualifiers)}"
                )

        for role in ranked_roles:
            yc_rows.append(build_yc_output_row(role, company_result, role))

            if role["final_route"] == "Skip":
                metrics["skip_count"] += 1
                if to_float(role.get("role_interest")) >= ROLE_INTEREST_THRESHOLD:
                    metrics["high_role_interest_but_skip"] += 1
                continue

            # Final safety gate before Notion write:
            # unknown title bucket must show unusually strong role-fit evidence.
            if (
                role.get("title_bucket", "unknown") == "unknown"
                and (
                    role_title_matches_exclusion_substrings(
                        role, UNKNOWN_BUCKET_TITLE_SUBSTRINGS
                    )
                    or to_float(role.get("role_fit_score")) < 7.6
                    or to_float(role.get("job_confidence")) < 7
                )
            ):
                role["final_route"] = "Skip"
                role["main_reservation"] = (
                    role.get("main_reservation")
                    or "Unknown title-bucket role did not show strong enough job-fit evidence."
                )
                metrics["skip_count"] += 1
                continue

            try:
                upsert_eval_job(role)
                print(f"→ Notion (YC): {role['company']} | {role['title']} | {role['final_route']}")

                if role["final_route"] == "Apply":
                    metrics["apply_writes"] += 1
                elif role["final_route"] == "Network":
                    metrics["network_writes"] += 1
                elif role["final_route"] == "Review":
                    metrics["review_writes"] += 1

            except Exception as e:
                msg = f"[yc_jobs] Notion write failed for {role.get('company')} | {role.get('title')}: {e}"
                print(msg)
                errors.append(msg)

    if yc_rows:
        with YC_OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=yc_rows[0].keys())
            writer.writeheader()
            writer.writerows(yc_rows)

        print(f"[yc_jobs] wrote {len(yc_rows)} rows to {YC_OUTPUT_CSV}")


def main():
    run_started_at = datetime.now(timezone.utc).isoformat()
    metrics = {
        "script": "company_discovery",
        "run_started_at": run_started_at,
        "run_finished_at": "",
        "status": "success",
        "active_companies": len([c for c in COMPANY_REGISTRY if c.get("enabled", True)]),
        "companies_checked": 0,
        "jobs_fetched": 0,
        "jobs_evaluated": 0,
        "apply_writes": 0,
        "network_writes": 0,
        "review_writes": 0,
        "skip_count": 0,
        "high_role_interest_but_skip": 0,
        "discovery_candidates": 0,
        "discovery_promoted": 0,
        "notes": "",
    }

    if is_new_config():
        candidate_profile = get_candidate_prompt()
        discovery_cfg = get_discovery_config()
        discovery_patterns = discovery_cfg["patterns"] or DISCOVERY_PATTERNS
        broad_titles = discovery_cfg["broad_sweep_titles"] or DEFAULT_BROAD_SWEEP_TITLES
        adjacent_kw = discovery_cfg["adjacent_title_keywords"] or DEFAULT_ADJACENT_TITLE_KEYWORDS
    else:
        candidate_profile = load_text_file(CANDIDATE_PROFILE_PATH)
        adjacent_kw, broad_titles = load_discovery_keywords(CANDIDATE_PROFILE_PATH)
        discovery_patterns = DISCOVERY_PATTERNS

    errors = []

    try:
        process_ats_company_discovery(
            candidate_profile,
            metrics,
            errors,
            broad_titles,
            adjacent_kw,
            discovery_patterns,
        )
        process_yc_job_discovery(candidate_profile, metrics, errors)

        if errors:
            metrics["status"] = "partial_failure"
            metrics["notes"] = " | ".join(errors[:10])

    except Exception as e:
        metrics["status"] = "failed"
        metrics["notes"] = str(e)
        raise

    finally:
        metrics["run_finished_at"] = datetime.now(timezone.utc).isoformat()
        append_run_metric(metrics)


if __name__ == "__main__":
    main()