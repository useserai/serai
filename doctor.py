"""Preflight checks for Serai. Validates Notion DB schema and write permissions
before long-running pipelines. Auto-runs from run_company_discovery.py and
eval_llm_scoring.py; also runnable standalone via `python doctor.py`."""

import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

EXPECTED_PROPERTIES = {
    "Name": "title",
    "Company": "rich_text",
    "Title": "rich_text",
    "URL": "url",
    "Location": "rich_text",
    "Final Recommendation": "select",
    "First Seen": "date",
    "Comp Min": "number",
    "Comp Max": "number",
    "Deep Eval Score": "number",
    "Company Score": "number",
    "Legitimacy": "select",
    "Why Strong": "rich_text",
    "Main Reservation": "rich_text",
    "Source": "rich_text",
    "Source Job ID": "rich_text",
}

REQUIRED_ENV_VARS = [
    "NOTION_TOKEN",
    "NOTION_DATABASE_ID",
    "OPENAI_API_KEY",
]

OPTIONAL_SEARCH_KEYS = [
    "BRAVE_SEARCH_API_KEY",
    "TAVILY_API_KEY",
]


def check_env_vars():
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        return False, f"Missing required env vars: {', '.join(missing)}"

    backend_status = []
    for var in OPTIONAL_SEARCH_KEYS:
        backend_status.append(f"{var}={'set' if os.getenv(var) else 'not set'}")
    backend_status.append("DuckDuckGo=always available (when ddgs installed)")

    return True, "Required env vars present. Search backends: " + " | ".join(backend_status)


def check_notion_schema():
    notion = Client(auth=os.getenv("NOTION_TOKEN"))
    database_id = os.getenv("NOTION_DATABASE_ID")

    try:
        database = notion.databases.retrieve(database_id=database_id)
    except Exception as e:
        return False, f"Cannot retrieve Notion database: {e}"

    # Try the multi-source API first; fall back to reading properties directly off the database
    properties = {}
    data_sources = database.get("data_sources", [])
    if data_sources:
        try:
            data_source = notion.data_sources.retrieve(data_source_id=data_sources[0]["id"])
            properties = data_source.get("properties", {})
        except Exception:
            properties = {}

    if not properties:
        properties = database.get("properties", {})

    if not properties:
        return False, "Could not read property schema from database"

    issues = []
    for prop_name, expected_type in EXPECTED_PROPERTIES.items():
        if prop_name not in properties:
            issues.append(f"missing '{prop_name}' (expected type: {expected_type})")
            continue
        actual_type = properties[prop_name].get("type")
        if actual_type != expected_type:
            issues.append(
                f"'{prop_name}' has type '{actual_type}', expected '{expected_type}'"
            )

    if issues:
        return False, "Schema mismatches:\n  - " + "\n  - ".join(issues)

    return True, f"Notion schema OK ({len(EXPECTED_PROPERTIES)} properties validated)"


def check_notion_write_permissions():
    notion = Client(auth=os.getenv("NOTION_TOKEN"))
    database_id = os.getenv("NOTION_DATABASE_ID")

    try:
        database = notion.databases.retrieve(database_id=database_id)
        data_sources = database.get("data_sources", [])
        if not data_sources:
            return False, "No data sources found on database"
        data_source_id = data_sources[0]["id"]

        test_page = notion.pages.create(
            parent={"data_source_id": data_source_id},
            properties={
                "Name": {
                    "title": [
                        {"text": {"content": "[serai doctor] write test - safe to delete"}}
                    ]
                }
            },
        )

        notion.pages.update(page_id=test_page["id"], archived=True)

        return True, f"Write OK (created+archived test page {test_page['id'][:8]}...)"
    except Exception as e:
        return False, f"Write test failed: {e}"


def run_preflight(verbose: bool = True) -> bool:
    checks = [
        ("Environment variables", check_env_vars),
        ("Notion DB schema", check_notion_schema),
        ("Notion write permissions", check_notion_write_permissions),
    ]

    all_passed = True

    if verbose:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n=== serai doctor — preflight checks ({ts}) ===\n")

    for name, check in checks:
        try:
            passed, message = check()
        except Exception as e:
            passed = False
            message = f"Check raised exception: {e}"

        status = "[PASS]" if passed else "[FAIL]"
        if verbose:
            print(f"{status}  {name}")
            print(f"        {message}\n")

        if not passed:
            all_passed = False

    if verbose:
        if all_passed:
            print("All preflight checks passed.\n")
        else:
            print("Preflight FAILED. Fix the issues above before running.\n")

    return all_passed


if __name__ == "__main__":
    success = run_preflight(verbose=True)
    sys.exit(0 if success else 1)
