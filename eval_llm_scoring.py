import csv
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

print("RUNNING eval_llm_scoring.py")

from job_source_router import fetch_jobs_for_company, fetch_job_detail_for_company
from company_registry import COMPANY_REGISTRY
from normalize import (
    normalize_job,
    extract_comp_from_greenhouse_detail,
    extract_workable_detail_fields,
    extract_smartrecruiters_detail_fields,
    extract_workday_detail_fields,
    fill_missing_comp,
    format_compensation,
)
from job_filter import fast_filter_title_geo, check_comp
from llm_company_score import llm_score_company
from role_eval import stage1_screen, stage2_deep_eval
from job_cache import load_cache, save_cache, upsert_cache_job, attach_first_seen_to_job
from job_freshness import compute_freshness
from notion_helper import upsert_eval_job
from run_metrics import append_run_metric
import company_registry
import config_loader

MAX_JOBS_PER_COMPANY = 100

print(f"=== RUN START {datetime.now()} ===")
print("EVAL FILE:", Path(__file__).resolve())
print("REGISTRY FILE:", Path(company_registry.__file__).resolve())
print("REGISTRY COUNT:", len(company_registry.COMPANY_REGISTRY))
print("REGISTRY SLUGS:", [c["company_slug"] for c in company_registry.COMPANY_REGISTRY if c.get("enabled", True)])


CANDIDATE_PROFILE = config_loader.get_candidate_prompt()


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_output_row(job: dict, company_result: dict, stage1: dict, stage2: Optional[dict]) -> dict:
    row = {
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
        "archetype": stage1.get("archetype", ""),
        "resume_match_score": stage1.get("resume_match_score", ""),
        "level_fit_score": stage1.get("level_fit_score", ""),
        "screen_score": stage1.get("screen_score", ""),
        "screen_route": stage1.get("screen_route", ""),
        "gap_severity": stage1.get("gap_severity", ""),
        "differentiation_reason": stage1.get("differentiation_reason", ""),
        "main_reservation": stage1.get("main_reservation", ""),
        "first_seen_at": job.get("first_seen_at", ""),
        "first_seen_age_days": job.get("first_seen_age_days", ""),
        "freshness_bucket": job.get("freshness_bucket", ""),
    }
    if stage2:
        row.update({
            "deep_eval_score": stage2.get("deep_eval_score", ""),
            "final_route": stage2.get("final_route", ""),
            "legitimacy_tier": stage2.get("legitimacy_tier", ""),
            "apply_urgency": stage2.get("apply_urgency", ""),
            "comp_assessment": stage2.get("comp_assessment", ""),
        })
    else:
        row.update({
            "deep_eval_score": "",
            "final_route": stage1.get("screen_route", "Skip"),
            "legitimacy_tier": "",
            "apply_urgency": "",
            "comp_assessment": "",
        })
    return row


def build_notion_payload(job: dict, company_result: dict, stage1: dict, stage2: Optional[dict]) -> dict:
    """Merge job + stage results into a flat dict for notion_helper.build_properties()."""
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
        # Stage 1 only — map screen_route to final_route for Notion
        route = stage1.get("screen_route", "Skip")
        payload["final_route"] = route if route == "Skip" else route
        payload["deep_eval_score"] = None
        payload["legitimacy_tier"] = None
        payload["apply_urgency"] = None
    return payload


def main():
    from doctor import run_preflight
    if not run_preflight():
        print("Preflight failed. Aborting.")
        sys.exit(1)

    run_started_at = datetime.now(timezone.utc).isoformat()
    metrics = {
        "script": "job_monitor",
        "run_started_at": run_started_at,
        "run_finished_at": "",
        "status": "success",
        "active_companies": len([c for c in COMPANY_REGISTRY if c.get("enabled", True)]),
        "companies_checked": 0,
        "jobs_fetched": 0,
        "jobs_screened": 0,
        "stage2_evaluated": 0,
        "notion_writes": 0,
        "skip_count": 0,
        "notes": "",
    }

    rows = []
    cache = load_cache()
    error_messages = []

    try:
        for company_config in COMPANY_REGISTRY:
            company_slug = company_config["company_slug"]

            try:
                if not company_config.get("enabled", True):
                    continue

                metrics["companies_checked"] += 1
                print(f"Checking company: {company_slug}")

                raw_jobs = fetch_jobs_for_company(company_config)
                metrics["jobs_fetched"] += len(raw_jobs)
                if not raw_jobs:
                    continue

                company_result = llm_score_company(company_slug, CANDIDATE_PROFILE)

                for raw_job in raw_jobs[:MAX_JOBS_PER_COMPANY]:
                    job = normalize_job(raw_job, company_config)

                    filter_result = fast_filter_title_geo(job)
                    upsert_cache_job(cache, job, filter_result["passed"])

                    if not filter_result["passed"]:
                        continue

                    job = attach_first_seen_to_job(cache, job)
                    job = compute_freshness(job)

                    job_detail = None

                    if company_config["source"] == "greenhouse":
                        job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                        comp_min, comp_max = extract_comp_from_greenhouse_detail(job_detail)
                        if comp_min is not None or comp_max is not None:
                            job["comp_min"] = comp_min
                            job["comp_max"] = comp_max

                    elif company_config["source"] == "workable":
                        job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                        detail_fields = extract_workable_detail_fields(job_detail)
                        if detail_fields["description"]:
                            job["description"] = detail_fields["description"]
                            job["job_description"] = detail_fields["description"]
                        if detail_fields["comp_min"] is not None or detail_fields["comp_max"] is not None:
                            job["comp_min"] = detail_fields["comp_min"]
                            job["comp_max"] = detail_fields["comp_max"]

                    elif company_config["source"] == "smartrecruiters":
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

                    elif company_config["source"] == "workday":
                        job_detail = fetch_job_detail_for_company(company_config, job["source_job_id"])
                        detail_fields = extract_workday_detail_fields(job_detail)
                        if detail_fields["description"]:
                            job["description"] = detail_fields["description"]
                            job["job_description"] = detail_fields["description"]
                        if detail_fields["location"] and job.get("location") in {"", "Unknown"}:
                            job["location"] = detail_fields["location"]
                        if detail_fields["url"] and not job.get("url"):
                            job["url"] = detail_fields["url"]
                        if detail_fields["comp_min"] is not None or detail_fields["comp_max"] is not None:
                            job["comp_min"] = detail_fields["comp_min"]
                            job["comp_max"] = detail_fields["comp_max"]

                    job = fill_missing_comp(job, job_detail=job_detail)
                    job["compensation"] = format_compensation(job.get("comp_min"), job.get("comp_max"))

                    comp_result = check_comp(job.get("comp_min"), job.get("comp_max"))
                    if not comp_result["passed"]:
                        continue

                    # ---- Stage 1: Screen ----
                    metrics["jobs_screened"] += 1
                    stage1 = stage1_screen(job, CANDIDATE_PROFILE, company_result)

                    screen_route = stage1.get("screen_route", "Skip")

                    if screen_route == "Skip":
                        metrics["skip_count"] += 1
                        rows.append(build_output_row(job, company_result, stage1, None))
                        continue

                    # ---- Stage 2: Deep Eval (Apply / Apply with Caution only) ----
                    metrics["stage2_evaluated"] += 1
                    stage2 = stage2_deep_eval(job, CANDIDATE_PROFILE, company_result, stage1)

                    final_route = stage2.get("final_route", "Do Not Apply")

                    if final_route == "Do Not Apply":
                        metrics["skip_count"] += 1
                        rows.append(build_output_row(job, company_result, stage1, stage2))
                        continue

                    # ---- Write to Notion (Strong Apply / Apply only) ----
                    try:
                        notion_payload = build_notion_payload(job, company_result, stage1, stage2)
                        upsert_eval_job(notion_payload)
                        metrics["notion_writes"] += 1
                        print(f"→ Notion: {job['company']} | {job['title']} | {final_route}")
                    except Exception as e:
                        msg = f"Notion error for {job.get('company')} | {job.get('title')}: {e}"
                        print(msg)
                        error_messages.append(msg)

                    rows.append(build_output_row(job, company_result, stage1, stage2))

            except Exception as e:
                msg = f"Error processing {company_slug}: {e}"
                print(msg)
                error_messages.append(msg)
                continue

            print("LOOPING COMPANY:", company_config["company_slug"], "| enabled:", company_config.get("enabled", True))

            # Incremental persistence: save cache + CSV after every company so failed runs preserve progress
            save_cache(cache)
            if rows:
                with open("llm_eval_results.csv", "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)

        if rows:
            print(f"\nWrote {len(rows)} rows to llm_eval_results.csv")
        else:
            print("No rows matched the filter.")

        save_cache(cache)

        if error_messages:
            metrics["status"] = "partial_failure"
            metrics["notes"] = " | ".join(error_messages[:10])

    except Exception as e:
        metrics["status"] = "failed"
        metrics["notes"] = str(e)
        raise

    finally:
        metrics["run_finished_at"] = datetime.now(timezone.utc).isoformat()
        append_run_metric(metrics)


if __name__ == "__main__":
    main()
