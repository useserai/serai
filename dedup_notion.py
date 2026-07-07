"""One-time cleanup of duplicate rows in the Notion job DB.

Groups pages by (Source, Source Job ID). For each duplicate group:
  1. Picks the OLDEST page (preserves first_seen_at for freshness scoring)
  2. Ports the newest scores/route onto it
  3. Archives all other duplicates

Buckets the fetch by Source to work around Notion's ~10k cursor-depth cap on
data_sources.query. Each Source bucket stays well under that cap.

Dry-run by default. Pass --apply to actually archive.

Usage:
    python dedup_notion.py            # dry-run report
    python dedup_notion.py --apply    # archive duplicates
"""

import argparse
import os
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not NOTION_TOKEN or not NOTION_DATABASE_ID:
    print("Missing NOTION_TOKEN or NOTION_DATABASE_ID in environment.", file=sys.stderr)
    sys.exit(1)

notion = Client(auth=NOTION_TOKEN)

# ~2.8 req/sec, comfortably under Notion's 3/sec average rate limit.
RATE_SLEEP = 0.35

# Sources Serai writes to Notion. If a page's Source is empty or unknown, it
# gets skipped (no dedup key possible anyway).
SOURCE_VALUES = ["greenhouse", "ashby", "lever", "workable", "smartrecruiters", "workday"]

# Properties to port from newest-scored page onto the oldest page.
SCORE_PROPS = [
    "Final Recommendation",
    "Deep Eval Score",
    "Company Score",
    "Legitimacy",
    "Why Strong",
    "Main Reservation",
    "Comp Min",
    "Comp Max",
    "URL",
    "Location",
]


def get_data_source_id() -> str:
    database = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
    data_sources = database.get("data_sources", [])
    if not data_sources:
        raise ValueError("No data sources found for this Notion database.")
    return data_sources[0]["id"]


DATA_SOURCE_ID = get_data_source_id()


def rich_text_value(prop: dict) -> str:
    if not prop:
        return ""
    rt = prop.get("rich_text") or []
    return rt[0].get("plain_text", "") if rt else ""


def _fetch_pages_for_source(source: str) -> list:
    pages = []
    cursor = None
    while True:
        kwargs = {
            "data_source_id": DATA_SOURCE_ID,
            "page_size": 100,
            "filter": {"property": "Source", "rich_text": {"equals": source}},
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.data_sources.query(**kwargs)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        time.sleep(RATE_SLEEP)
    return pages


def fetch_all_pages() -> list:
    """Fetch pages bucketed by Source. Notion's data_sources.query has a soft
    cursor-depth cap around 10k rows even with has_more: False — filtering by
    Source keeps each bucket under that cap. Six calls instead of one paginator.
    """
    all_pages = []
    for source in SOURCE_VALUES:
        pages = _fetch_pages_for_source(source)
        print(f"  Source={source}: {len(pages)} pages")
        all_pages.extend(pages)
    return all_pages


def group_by_source_key(pages: list) -> dict:
    groups = defaultdict(list)
    for page in pages:
        props = page["properties"]
        source = rich_text_value(props.get("Source"))
        source_job_id = rich_text_value(props.get("Source Job ID"))
        if not source or not source_job_id:
            continue  # un-dedupable, skip
        groups[(source, source_job_id)].append(page)
    return groups


def has_any_score(page: dict) -> bool:
    props = page["properties"]
    for name in SCORE_PROPS:
        prop = props.get(name)
        if not prop:
            continue
        ptype = prop.get("type")
        if ptype and prop.get(ptype) not in (None, [], ""):
            return True
    return False


def newest_with_scores(pages: list) -> dict:
    """Pick the newest page (by created_time desc) that carries any score, else the newest overall."""
    scored = [p for p in pages if has_any_score(p)]
    candidates = scored if scored else pages
    return max(candidates, key=lambda p: p.get("created_time", ""))


def build_port_payload(source_page: dict) -> dict:
    payload = {}
    src_props = source_page["properties"]
    for name in SCORE_PROPS:
        prop = src_props.get(name)
        if not prop:
            continue
        ptype = prop.get("type")
        if not ptype:
            continue
        value = prop.get(ptype)
        if value in (None, [], ""):
            continue
        payload[name] = {ptype: value}
    return payload


def port_scores(target_page_id: str, source_page: dict) -> None:
    payload = build_port_payload(source_page)
    if payload:
        notion.pages.update(page_id=target_page_id, properties=payload)


def archive_page(page_id: str) -> None:
    notion.pages.update(page_id=page_id, archived=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually archive duplicates (default: dry-run)")
    args = parser.parse_args()

    print("Fetching all pages...")
    pages = fetch_all_pages()
    print(f"Fetched {len(pages)} pages")

    groups = group_by_source_key(pages)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    total_dupes = sum(len(v) - 1 for v in dup_groups.values())

    print(f"Found {len(dup_groups)} keys with duplicates")
    print(f"{total_dupes} duplicate pages would be archived")

    if not dup_groups:
        return

    top = sorted(dup_groups.items(), key=lambda kv: -len(kv[1]))[:10]
    print("\nTop 10 offenders:")
    for (src, sjid), pgs in top:
        print(f"  {src}::{sjid} - {len(pgs)} rows")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to archive.")
        return

    print("\nApplying...")
    archived_count = 0
    for i, ((src, sjid), group_pages) in enumerate(dup_groups.items(), 1):
        sorted_pgs = sorted(group_pages, key=lambda p: p.get("created_time", ""))
        keep = sorted_pgs[0]
        rest = sorted_pgs[1:]

        newest = newest_with_scores(group_pages)
        if newest["id"] != keep["id"]:
            try:
                port_scores(keep["id"], newest)
                time.sleep(RATE_SLEEP)
            except Exception as e:
                print(f"  port failed {src}::{sjid}: {e}")

        for dup in rest:
            try:
                archive_page(dup["id"])
                archived_count += 1
                time.sleep(RATE_SLEEP)
            except Exception as e:
                print(f"  archive failed {dup['id']}: {e}")

        if i % 25 == 0:
            print(f"  processed {i}/{len(dup_groups)} groups, archived {archived_count} so far")

    print(f"\nDone. Archived {archived_count} pages, preserved {len(dup_groups)} originals with newest scores.")


if __name__ == "__main__":
    main()
