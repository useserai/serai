import re
from typing import Optional

REQUIRED_NORMALIZED_FIELDS = [
    "company",
    "title",
    "location",
    "url",
    "source",
    "source_job_id",
    "description",
    "job_description",
    "comp_min",
    "comp_max",
    "compensation",
    "first_seen_at",
    "first_seen_age_days",
    "freshness_bucket",
]


def validate_normalized_job(job):
    missing = [field for field in REQUIRED_NORMALIZED_FIELDS if field not in job]
    if missing:
        raise ValueError(f"Missing normalized job fields: {missing}")
    return job


COMP_RE = re.compile(
    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})?(?:\.[0-9]+)?)\s*([kK]?)"
    r"\s*(?:-|to|–|—)\s*"
    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})?(?:\.[0-9]+)?)\s*([kK]?)",
    re.IGNORECASE,
)

SINGLE_COMP_RE = re.compile(
    r"(?:starting at|from|minimum of|at least|up to)\s+\$?\s*([0-9]{2,3}(?:,[0-9]{3})?(?:\.[0-9]+)?)\s*([kK]?)",
    re.IGNORECASE,
)


def _to_number(value_str, k_suffix):
    value = float(value_str.replace(",", ""))
    if k_suffix:
        value *= 1000
    return value


def extract_comp_from_text(text):
    if not text:
        return None, None

    match = COMP_RE.search(text)
    if match:
        low = _to_number(match.group(1), match.group(2))
        high = _to_number(match.group(3), match.group(4))
        return low, high

    single = SINGLE_COMP_RE.search(text)
    if single:
        val = _to_number(single.group(1), single.group(2))
        prefix = single.group(0).lower()
        if "up to" in prefix:
            return None, val
        return val, None

    return None, None


def _normalize_money_text(value: Optional[str]):
    if not value:
        return None

    text = value.strip().replace(",", "")
    multiplier = 1

    if text[-1:].lower() == "k":
        multiplier = 1000
        text = text[:-1].strip()
    elif text[-1:].lower() == "m":
        multiplier = 1_000_000
        text = text[:-1].strip()
    elif text[-1:].lower() == "b":
        multiplier = 1_000_000_000
        text = text[:-1].strip()

    text = re.sub(r"^[^\d]+", "", text)

    try:
        return float(text) * multiplier
    except ValueError:
        return None


def format_compensation(comp_min, comp_max):
    if comp_min is not None and comp_max is not None:
        return f"${int(comp_min):,} - ${int(comp_max):,}"
    if comp_min is not None:
        return f"${int(comp_min):,}+"
    if comp_max is not None:
        return f"Up to ${int(comp_max):,}"
    return ""


def normalize_greenhouse_job(raw_job, company_slug):
    title = raw_job.get("title", "No title")
    source_job_id = str(raw_job.get("id", ""))
    url = raw_job.get("absolute_url", "")
    description = raw_job.get("content", "") or ""

    location = "Unknown"
    if raw_job.get("location"):
        location = raw_job["location"].get("name", "Unknown")

    comp_min, comp_max = extract_comp_from_text(description)

    return {
        "company": company_slug.lower(),
        "title": title.strip(),
        "location": location.strip(),
        "url": url,
        "source": "greenhouse",
        "source_job_id": source_job_id,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "compensation": format_compensation(comp_min, comp_max),
        "description": description,
        "job_description": description,
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def normalize_ashby_job(raw_job, company_slug):
    title = (raw_job.get("title") or "No title").strip()
    url = raw_job.get("jobUrl") or raw_job.get("applyUrl") or ""
    source_job_id = str(
        raw_job.get("id")
        or raw_job.get("jobUrl")
        or raw_job.get("applyUrl")
        or title
    )
    description = raw_job.get("descriptionPlain") or ""

    raw_location = raw_job.get("location") or "Unknown"
    location = raw_location.strip() if isinstance(raw_location, str) else str(raw_location)

    comp_min = None
    comp_max = None

    compensation = raw_job.get("compensation") or {}
    summary_components = compensation.get("summaryComponents") or []

    for component in summary_components:
        if component.get("compensationType") == "Salary":
            comp_min = component.get("minValue")
            comp_max = component.get("maxValue")
            break

    if comp_min is None and comp_max is None:
        comp_min, comp_max = extract_comp_from_text(description)

    return {
        "company": company_slug.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "ashby",
        "source_job_id": source_job_id,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "compensation": format_compensation(comp_min, comp_max),
        "description": description,
        "job_description": description,
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def normalize_lever_job(raw_job, company_slug):
    title = (raw_job.get("text") or "No title").strip()
    url = raw_job.get("hostedUrl") or raw_job.get("applyUrl") or ""
    source_job_id = str(raw_job.get("id") or url or title)
    description = raw_job.get("descriptionPlain") or raw_job.get("description") or ""

    categories = raw_job.get("categories") or {}
    raw_location = categories.get("location") or "Unknown"
    location = raw_location.strip() if isinstance(raw_location, str) else str(raw_location)

    comp_min = None
    comp_max = None

    salary = raw_job.get("salaryRange")
    if isinstance(salary, dict):
        comp_min = salary.get("min")
        comp_max = salary.get("max")

    if comp_min is None and comp_max is None:
        comp_min, comp_max = extract_comp_from_text(description)

    return {
        "company": company_slug.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "lever",
        "source_job_id": source_job_id,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "compensation": format_compensation(comp_min, comp_max),
        "description": description,
        "job_description": description,
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def normalize_workable_job(raw_job, company_slug):
    title = (raw_job.get("title") or "No title").strip()
    shortcode = str(raw_job.get("shortcode") or raw_job.get("id") or title)
    url = raw_job.get("url") or f"https://apply.workable.com/{company_slug}/j/{shortcode}/"

    country = (raw_job.get("country") or "").strip()
    city = (raw_job.get("city") or "").strip()
    state = (raw_job.get("state") or "").strip()
    location_parts = [p for p in (city, state, country) if p]
    location = ", ".join(location_parts) or "Unknown"

    # Description and comp come from the .md detail endpoint — see extract_workable_detail_fields.
    return {
        "company": company_slug.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "workable",
        "source_job_id": shortcode,
        "comp_min": None,
        "comp_max": None,
        "compensation": "",
        "description": "",
        "job_description": "",
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def extract_workable_detail_fields(job_detail):
    """Parse the markdown payload returned by sources.workable.get_workable_job_detail.
    Workable's .md format includes a `**Salary:** USD MIN-MAX` line we extract structured
    comp from, with extract_comp_from_text as a fallback for less structured cases."""
    markdown = job_detail.get("markdown") or ""

    if not markdown:
        return {
            "description": "",
            "comp_min": None,
            "comp_max": None,
        }

    comp_min = None
    comp_max = None

    salary_match = re.search(
        r"\*\*Salary:\*\*\s*[A-Z]{0,3}\s*\$?([\d,]+(?:\.\d+)?)\s*[kK]?\s*[–\-—]+\s*\$?([\d,]+(?:\.\d+)?)\s*[kK]?",
        markdown,
    )
    if salary_match:
        try:
            comp_min = float(salary_match.group(1).replace(",", ""))
            comp_max = float(salary_match.group(2).replace(",", ""))
        except (ValueError, TypeError):
            comp_min = None
            comp_max = None

    if comp_min is None and comp_max is None:
        comp_min, comp_max = extract_comp_from_text(markdown)

    return {
        "description": markdown,
        "comp_min": comp_min,
        "comp_max": comp_max,
    }


def normalize_smartrecruiters_job(raw_job, company_slug):
    title = (raw_job.get("name") or "No title").strip()
    source_job_id = str(raw_job.get("id") or raw_job.get("uuid") or title)
    url = raw_job.get("postingUrl") or raw_job.get("applyUrl") or ""

    location_obj = raw_job.get("location") or {}
    location = (
        location_obj.get("fullLocation")
        or ", ".join([
            p for p in (
                location_obj.get("city") or "",
                location_obj.get("region") or "",
                location_obj.get("country") or "",
            )
            if p
        ])
        or "Unknown"
    )

    # Description and comp come from get_smartrecruiters_job_detail.
    return {
        "company": company_slug.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "smartrecruiters",
        "source_job_id": source_job_id,
        "comp_min": None,
        "comp_max": None,
        "compensation": "",
        "description": "",
        "job_description": "",
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def extract_smartrecruiters_detail_fields(job_detail):
    """Concatenate jobAd.sections into a single description and extract comp from the
    combined text. SmartRecruiters buries salary in HTML inside additionalInformation;
    extract_comp_from_text handles that."""
    sections = (job_detail.get("jobAd") or {}).get("sections") or {}

    parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key) or {}
        text = section.get("text") or ""
        if text:
            parts.append(text)

    description = "\n\n".join(parts)
    description = re.sub(r"<[^>]+>", " ", description)  # strip HTML tags
    description = re.sub(r"\s+", " ", description).strip()

    comp_min, comp_max = extract_comp_from_text(description)

    url = job_detail.get("postingUrl") or job_detail.get("applyUrl") or ""

    return {
        "description": description,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "url": url,
    }


def normalize_workday_job(raw_job, company_slug):
    title = (raw_job.get("title") or "No title").strip()
    source_job_id = str(raw_job.get("externalPath") or raw_job.get("bulletFields") or title)
    url = raw_job.get("applyUrl") or ""
    location = raw_job.get("locationsText") or "Unknown"
    description = raw_job.get("jobDescription") or ""

    comp_min, comp_max = extract_comp_from_text(description)

    return {
        "company": company_slug.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "workday",
        "source_job_id": source_job_id,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "compensation": format_compensation(comp_min, comp_max),
        "description": description,
        "job_description": description,
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def normalize_yc_job(raw_job, company_slug=None):
    derived_company = (
        company_slug
        or raw_job.get("company_slug")
        or raw_job.get("company_name", "yc_company").lower().replace(" ", "-")
    )

    title = (raw_job.get("title") or "No title").strip()
    source_job_id = str(raw_job.get("yc_job_id") or raw_job.get("job_url") or title)
    url = raw_job.get("job_url") or raw_job.get("apply_url") or ""
    description = raw_job.get("job_description") or ""

    location = raw_job.get("location") or "Unknown"

    comp_min = _normalize_money_text(raw_job.get("salary_min_text"))
    comp_max = _normalize_money_text(raw_job.get("salary_max_text"))

    if comp_min is None and comp_max is None:
        comp_min, comp_max = extract_comp_from_text(raw_job.get("salary_text") or "")
    if comp_min is None and comp_max is None:
        comp_min, comp_max = extract_comp_from_text(description)

    return {
        "company": derived_company.lower(),
        "title": title,
        "location": location,
        "url": url,
        "source": "yc_jobs",
        "source_job_id": source_job_id,
        "comp_min": comp_min,
        "comp_max": comp_max,
        "compensation": format_compensation(comp_min, comp_max),
        "description": description,
        "job_description": description,
        "first_seen_at": None,
        "first_seen_age_days": None,
        "freshness_bucket": None,
    }


def normalize_job(raw_job, company_config):
    source = company_config["source"]
    company_slug = company_config["company_slug"]

    if source == "greenhouse":
        job = normalize_greenhouse_job(raw_job, company_slug)
    elif source == "ashby":
        job = normalize_ashby_job(raw_job, company_slug)
    elif source == "lever":
        job = normalize_lever_job(raw_job, company_slug)
    elif source == "workable":
        job = normalize_workable_job(raw_job, company_slug)
    elif source == "smartrecruiters":
        job = normalize_smartrecruiters_job(raw_job, company_slug)
    elif source == "workday":
        job = normalize_workday_job(raw_job, company_slug)
    elif source == "yc_jobs":
        job = normalize_yc_job(raw_job, company_slug)
    else:
        raise ValueError(f"Unsupported source: {source}")

    return validate_normalized_job(job)


def extract_comp_from_greenhouse_detail(job_detail):
    pay_ranges = job_detail.get("pay_input_ranges", [])

    if not pay_ranges:
        return None, None

    first_range = pay_ranges[0]
    comp_min = first_range.get("min_cents")
    comp_max = first_range.get("max_cents")

    if comp_min is not None:
        comp_min = comp_min / 100
    if comp_max is not None:
        comp_max = comp_max / 100

    return comp_min, comp_max


def extract_workday_detail_fields(job_detail):
    info = job_detail.get("jobPostingInfo", {}) if isinstance(job_detail, dict) else {}

    description_parts = [
        info.get("jobDescription"),
        info.get("additionalJobDescription"),
    ]
    description = "\n\n".join([part for part in description_parts if part]) or ""

    comp_min, comp_max = extract_comp_from_text(description)

    return {
        "description": description,
        "location": info.get("location") or "",
        "url": info.get("externalUrl") or "",
        "comp_min": comp_min,
        "comp_max": comp_max,
    }


def fill_missing_comp(job, job_detail=None):
    existing_min = job.get("comp_min")
    existing_max = job.get("comp_max")

    if existing_min is not None or existing_max is not None:
        job["compensation"] = format_compensation(existing_min, existing_max)
        return job

    detail_text = ""
    if isinstance(job_detail, dict):
        detail_text = (
            job_detail.get("content")
            or job_detail.get("description")
            or job_detail.get("job_description")
            or ""
        )

    text_sources = [
        detail_text,
        job.get("description", ""),
        job.get("job_description", ""),
    ]

    for text in text_sources:
        comp_min, comp_max = extract_comp_from_text(text)
        if comp_min is not None or comp_max is not None:
            job["comp_min"] = comp_min
            job["comp_max"] = comp_max
            job["compensation"] = format_compensation(comp_min, comp_max)
            return job

    job["compensation"] = ""
    return job
