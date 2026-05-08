import csv
import sys
import config_loader
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
)

from config_loader import is_new_config, get_discovery_config, get_candidate_prompt, get_discovery_keywords, get_unknown_bucket_title_substrings_from_profile
from role_title_gates import (
    parse_unknown_bucket_title_substrings,
    role_title_matches_exclusion_substrings,
)
from llm_company_score import llm_score_company
from run_metrics import append_run_metric
from notion_helper import upsert_eval_job
from role_eval import stage1_screen, stage2_deep_eval
from job_filter import fast_filter_title_geo, check_comp
from normalize import normalize_yc_job
from sources.yc_jobs import get_yc_jobs

OUTPUT_CSV = Path("discovered_company_results.csv")
YC_OUTPUT_CSV = Path("yc_discovered_job_results.csv")
UNKNOWN_BUCKET_TITLE_SUBSTRINGS = parse_unknown_bucket_title_substrings(
    get_unknown_bucket_title_substrings_from_profile()
)
PROMOTION_THRESHOLD = 7.0
WATCHLIST_THRESHOLD = 6.5


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_discovery_notion_payload(job: dict, company_result: dict, company_slug: str) -> dict:
    """Build a minimal Notion payload for a job surfaced by discovery (no stage1/stage2 yet)."""
    return {
        **job,
        "company": company_slug,
        "company_interest_score": company_result.get("company_interest_score"),
        "final_route": "Discovery — Lead",
        "differentiation_reason": company_result.get("company_interest_reason", ""),
    }


def build_yc_notion_payload(job: dict, company_result: dict, stage1: dict, stage2: dict = None) -> dict:
    """Merge job + stage results into a flat dict for notion_helper, matching eval_llm_scoring pattern."""
    payload = {
        **job,
        "company_interest_score": company_result.get("company_interest_score"),
        "archetype": stage1.get("archetype", "Other"),
        "resume_match_score": stage1.get("resume_match_score"),
        "level_fit_score": stage1.get("level_fit_score"),
        "screen_score": stage1.get("screen_score"),
        "screen_route": stage1.get("screen_route", "Skip"),
        "differentiation_reason": stage1.get("differentiation_reason", ""),
        "main_reservation": stage1.get("main_reservation", ""),
    }
    if stage2:
        payload.update({
            "deep_eval_score": stage2.get("deep_eval_score"),
            "final_route": stage2.get("final_route", "Do Not Apply"),
            "legitimacy_tier": stage2.get("legitimacy_tier"),
            "apply_urgency": stage2.get("apply_urgency"),
        })
    else:
        route = stage1.get("screen_route", "Skip")
        payload["final_route"] = route if route == "Skip" else route
        payload["deep_eval_score"] = None
        payload["legitimacy_tier"] = None
        payload["apply_urgency"] = None
    return payload


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

    # High-scoring companies (>= WATCHLIST_THRESHOLD) get watchlist treatment even without
    # surfaced roles. Brave's role surface for any single run is incomplete; a company worth
    # monitoring shouldn't get dropped just because no role matched today's search results.
    # eval_llm_scoring will pick up roles when they appear.
    if company_score >= WATCHLIST_THRESHOLD:
        return "watchlist_company_only"

    return "rejected_low_company_fit"


def maybe_promote_company(item: dict, company_result: dict, status: str) -> bool:
    # promoted_* companies are added to active_companies.json AND get Notion writes for direct matches.
    # watchlist_* companies are added to active_companies.json (for daily monitoring, since they're
    # likely to hire soon) but do NOT get Notion writes — eval_llm_scoring.py will write them later
    # when actual matching roles surface.
    promotable_statuses = {
        "promoted_to_active",
        "promoted_to_active_company_only",
        "promoted_to_active_with_strong_roles",
        "promoted_to_active_with_adjacent_roles",
        "watchlist_company_only",
        "watchlist_with_adjacent_roles",
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
    rows = []
    discovered_store = load_discovered_companies()
    discovered_items = []  # accumulated for persist_discovery_results at end of run
    in_run_company_scores: dict = {}  # dedup LLM scoring within a single run

    # Stream over candidates: each is scored + written to Notion + persisted to disk
    # before the next is verified. Time-to-first-Notion-write is minutes, not hours.
    # Crashes mid-sweep preserve every candidate already processed.
    for item in discover_companies(
        discovery_patterns or DISCOVERY_PATTERNS,
        broad_sweep_titles,
        adjacent_title_keywords,
    ):
        metrics["discovery_candidates"] += 1
        metrics["companies_checked"] += 1
        discovered_items.append(item)

        company_slug = item["company_slug"]

        # Same company can surface as multiple candidates (e.g., Stripe on both
        # Greenhouse and Ashby). Dedup LLM scoring within this run.
        if company_slug in in_run_company_scores:
            company_result = in_run_company_scores[company_slug]
        else:
            company_result = llm_score_company(company_slug, candidate_profile)
            in_run_company_scores[company_slug] = company_result
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

        # Per-company Notion writes: only for promoted companies (with direct matches).
        # Watchlist companies are added to active_companies.json instead, so eval_llm_scoring
        # can pick up their roles when they hire.
        if status.startswith("promoted_"):
            for match in item.get("direct_matches", []):
                try:
                    notion_payload = build_discovery_notion_payload(match, company_result, company_slug)
                    upsert_eval_job(notion_payload)
                    metrics["discovery_notion_writes"] += 1
                    print(f"→ Notion (discovery): {company_slug} | {match.get('title')}")
                except Exception as e:
                    msg = f"[discovery] Notion write failed for {company_slug} | {match.get('title')}: {e}"
                    print(msg)
                    errors.append(msg)

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

        # Incremental persistence: save after every company so failed runs preserve progress
        save_discovered_companies(discovered_store)
        if rows:
            with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

    save_discovered_companies(discovered_store)

    if not discovered_items:
        print("[discovery] no candidate companies found")
        return

    persist_discovery_results(
        [
            {
                **item,
                "status": discovered_store[get_store_key(item)]["status"],
            }
            for item in discovered_items
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
    yc_rows = []

    for raw_job in raw_yc_jobs:
        try:
            job = normalize_yc_job(raw_job)

            filter_result = fast_filter_title_geo(job)
            if not filter_result["passed"]:
                continue

            comp_result = check_comp(job.get("comp_min"), job.get("comp_max"))
            if not comp_result["passed"]:
                continue

            company_slug = job["company"]
            if company_slug not in company_scores:
                company_scores[company_slug] = llm_score_company(company_slug, candidate_profile)

            company_result = company_scores[company_slug]

            # Check company disqualifiers before spending LLM calls
            company_disqualifiers = company_result.get("disqualifier_flags", []) or []
            if company_disqualifiers:
                metrics["skip_count"] += 1
                continue

            # ---- Stage 1: Screen ----
            metrics["jobs_evaluated"] += 1
            stage1 = stage1_screen(job, candidate_profile, company_result)

            screen_route = stage1.get("screen_route", "Skip")

            if screen_route == "Skip":
                metrics["skip_count"] += 1
                yc_rows.append(build_yc_notion_payload(job, company_result, stage1, None))
                continue

            # ---- Stage 2: Deep Eval (Apply / Apply with Caution only) ----
            stage2 = stage2_deep_eval(job, candidate_profile, company_result, stage1)

            final_route = stage2.get("final_route", "Do Not Apply")

            if final_route == "Do Not Apply":
                metrics["skip_count"] += 1
                yc_rows.append(build_yc_notion_payload(job, company_result, stage1, stage2))
                continue

            # ---- Write to Notion (Strong Apply / Apply only) ----
            try:
                notion_payload = build_yc_notion_payload(job, company_result, stage1, stage2)
                upsert_eval_job(notion_payload)
                metrics["apply_writes"] += 1
                print(f"→ Notion (YC): {job['company']} | {job['title']} | {final_route}")
            except Exception as e:
                msg = f"[yc_jobs] Notion write failed for {job.get('company')} | {job.get('title')}: {e}"
                print(msg)
                errors.append(msg)

            yc_rows.append(build_yc_notion_payload(job, company_result, stage1, stage2))

        except Exception as e:
            msg = f"[yc_jobs] failed to evaluate {raw_job.get('job_url', 'unknown')}: {e}"
            print(msg)
            errors.append(msg)

    if yc_rows:
        with YC_OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=yc_rows[0].keys())
            writer.writeheader()
            writer.writerows(yc_rows)

        print(f"[yc_jobs] wrote {len(yc_rows)} rows to {YC_OUTPUT_CSV}")

def main():
    from doctor import run_preflight
    if not run_preflight():
        print("Preflight failed. Aborting.")
        sys.exit(1)

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
        "skip_count": 0,
        "high_role_interest_but_skip": 0,
        "discovery_candidates": 0,
        "discovery_promoted": 0,
        "discovery_notion_writes": 0,
        "notes": "",
    }

    if is_new_config():
        candidate_profile = get_candidate_prompt()
        discovery_cfg = get_discovery_config()
        discovery_patterns = discovery_cfg["patterns"] or DISCOVERY_PATTERNS
        broad_titles = discovery_cfg["broad_sweep_titles"] or DEFAULT_BROAD_SWEEP_TITLES
        adjacent_kw = discovery_cfg["adjacent_title_keywords"] or DEFAULT_ADJACENT_TITLE_KEYWORDS
    else:
        candidate_profile = config_loader.get_candidate_prompt()
        adjacent_kw, broad_titles = config_loader.get_discovery_keywords()
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