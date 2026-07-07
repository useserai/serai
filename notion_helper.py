print("LOADING NOTION FILE")

import os
from datetime import datetime

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not NOTION_TOKEN:
    raise ValueError("Missing NOTION_TOKEN in environment")
if not NOTION_DATABASE_ID:
    raise ValueError("Missing NOTION_DATABASE_ID in environment")

notion = Client(auth=NOTION_TOKEN)


def safe_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def truncate_text(value, max_len=1900):
    if not value:
        return ""
    return str(value)[:max_len]


def get_data_source_id():
    database = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)

    data_sources = database.get("data_sources", [])
    if not data_sources:
        raise ValueError("No data sources found for this Notion database.")

    return data_sources[0]["id"]


NOTION_DATA_SOURCE_ID = get_data_source_id()


def get_job_page_id(source, source_job_id):
    """Look up an existing Notion page by (Source, Source Job ID).

    Server-side filter. The previous version pulled the first 100 pages of the
    entire data source and iterated in Python — Notion caps query results at 100,
    so dedup silently broke once the DB grew past that. This filter returns only
    the matching page(s), independent of DB size.
    """
    response = notion.data_sources.query(
        data_source_id=NOTION_DATA_SOURCE_ID,
        filter={
            "and": [
                {"property": "Source", "rich_text": {"equals": source}},
                {"property": "Source Job ID", "rich_text": {"equals": str(source_job_id)}},
            ]
        },
        page_size=1,
    )
    results = response.get("results", [])
    if results:
        return results[0]["id"]
    return None


def build_properties(job):
    comp_min = safe_number(job.get("comp_min"))
    comp_max = safe_number(job.get("comp_max"))

    properties = {
        "Name": {
            "title": [
                {
                    "text": {
                        "content": truncate_text(
                            f"{job.get('company', '')} - {job.get('title', '')}",
                            200
                        )
                    }
                }
            ]
        },
        "Company": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("company", ""), 200)
                    }
                }
            ] if job.get("company") else []
        },
        "Title": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("title", ""), 200)
                    }
                }
            ] if job.get("title") else []
        },
        "Final Recommendation": {
            "select": {
                "name": job.get("final_route", "Skip")
            }
        },
        "First Seen": {
            "date": {
                "start": job.get("first_seen_at") or datetime.now().isoformat()
            }
        },
        "Location": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("location", ""), 200)
                    }
                }
            ] if job.get("location") else []
        },
        "URL": {
            "url": job.get("url") or None
        },
        "Comp Min": {
            "number": comp_min
        },
        "Comp Max": {
            "number": comp_max
        },
        "Deep Eval Score": {
            "number": safe_number(job.get("deep_eval_score"))
        },
        "Company Score": {
            "number": safe_number(job.get("company_interest_score"))
        },
        "Legitimacy": {
            "select": {
                "name": job.get("legitimacy_tier", "")
            }
        } if job.get("legitimacy_tier") else {},
        "Why Strong": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("differentiation_reason", ""))
                    }
                }
            ] if job.get("differentiation_reason") else []
        },
        "Main Reservation": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("main_reservation", ""))
                    }
                }
            ] if job.get("main_reservation") else []
        },
        "Source": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(job.get("source", ""), 100)
                    }
                }
            ] if job.get("source") else []
        },
        "Source Job ID": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate_text(str(job.get("source_job_id", "")), 100)
                    }
                }
            ] if job.get("source_job_id") else []
        },
    }

    properties = {k: v for k, v in properties.items() if v}

    return properties


def create_job_page(job):
    response = notion.pages.create(
        parent={"data_source_id": NOTION_DATA_SOURCE_ID},
        properties={
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": truncate_text(
                                f"{job.get('company', '')} - {job.get('title', '')}",
                                200
                            )
                        }
                    }
                ]
            },
            "Source": {
                "rich_text": [
                    {
                        "text": {
                            "content": truncate_text(job.get("source", ""), 100)
                        }
                    }
                ] if job.get("source") else []
            },
            "Source Job ID": {
                "rich_text": [
                    {
                        "text": {
                            "content": truncate_text(str(job.get("source_job_id", "")), 100)
                        }
                    }
                ] if job.get("source_job_id") else []
            },
        }
    )
    return response["id"]


def update_eval_result(page_id, job):
    properties = build_properties(job)
    notion.pages.update(
        page_id=page_id,
        properties=properties
    )


def upsert_eval_job(job):
    source = str(job.get("source", "")).strip()
    source_job_id = str(job.get("source_job_id", "")).strip()

    if not source:
        raise ValueError(f"Missing source for job: {job.get('company')} | {job.get('title')}")
    if not source_job_id:
        raise ValueError(f"Missing source_job_id for job: {job.get('company')} | {job.get('title')}")

    page_id = get_job_page_id(source, source_job_id)

    if not page_id:
        page_id = create_job_page(job)

    update_eval_result(page_id, job)
    return page_id
