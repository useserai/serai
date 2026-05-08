import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ACTIVE_COMPANIES_FILE = BASE_DIR / "active_companies.json"


def _normalize_company_record(record: dict) -> dict:
    source = (record.get("source") or "").strip().lower()
    company_slug = (record.get("company_slug") or "").strip().lower()
    enabled = bool(record.get("enabled", True))

    if not company_slug:
        raise ValueError(f"Invalid company record missing company_slug: {record}")

    if source not in {"greenhouse", "ashby", "lever", "workable", "smartrecruiters", "workday"}:
        raise ValueError(f"Invalid or unsupported source for {company_slug}: {source}")

    normalized = {
        "company_slug": company_slug,
        "source": source,
        "enabled": enabled,
    }

    if source in {"greenhouse", "ashby", "lever", "workable", "smartrecruiters"}:
        board_token = (record.get("board_token") or "").strip()
        if not board_token:
            raise ValueError(f"Missing board_token for {company_slug}")
        normalized["board_token"] = board_token

    if source == "workday":
        workday_host = (record.get("workday_host") or "").strip()
        workday_site = (record.get("workday_site") or "").strip()
        workday_locale = (record.get("workday_locale") or "").strip()

        if not workday_host or not workday_site:
            raise ValueError(
                f"Missing workday_host / workday_site for {company_slug}"
            )

        normalized["workday_host"] = workday_host
        normalized["workday_site"] = workday_site

        if workday_locale:
            normalized["workday_locale"] = workday_locale

    for optional_key in ["company_name", "board_url", "notes", "discovered_at"]:
        if record.get(optional_key) is not None:
            normalized[optional_key] = record.get(optional_key)

    return normalized


def _company_key(company: dict) -> tuple:
    if company["source"] == "workday":
        return (
            company["source"],
            company["workday_host"],
            company.get("workday_locale", ""),
            company["workday_site"],
        )
    return (company["source"], company.get("board_token", "").lower())


def load_company_registry() -> list[dict]:
    if not ACTIVE_COMPANIES_FILE.exists():
        return []

    raw = json.loads(ACTIVE_COMPANIES_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{ACTIVE_COMPANIES_FILE} must contain a JSON list")

    normalized = []
    seen = set()

    for record in raw:
        if not isinstance(record, dict):
            continue
        company = _normalize_company_record(record)
        key = _company_key(company)

        if key in seen:
            continue

        seen.add(key)
        normalized.append(company)

    return normalized


def save_company_registry(companies: list[dict]) -> None:
    cleaned = []
    seen = set()

    for company in companies:
        normalized = _normalize_company_record(company)
        key = _company_key(normalized)

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(normalized)

    cleaned = sorted(cleaned, key=lambda x: (x["source"], x["company_slug"]))
    ACTIVE_COMPANIES_FILE.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def upsert_active_company(company: dict) -> bool:
    companies = load_company_registry()
    normalized = _normalize_company_record(company)

    for existing in companies:
        if _company_key(existing) == _company_key(normalized):
            changed = False
            for key, value in normalized.items():
                if existing.get(key) != value:
                    existing[key] = value
                    changed = True
            if changed:
                save_company_registry(companies)
            return changed

    companies.append(normalized)
    save_company_registry(companies)
    return True


def active_company_keys() -> set[tuple]:
    return {_company_key(company) for company in load_company_registry()}


COMPANY_REGISTRY = load_company_registry()