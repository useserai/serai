print("LOADING JOB FILTER FILE")

import re

import config_loader

# Loaded once; override via === JOB FILTER CONFIG === JSON in candidate_data/candidate_profile.txt
_FILTER_CONFIG = config_loader.get_filter_config()

DEBUG_GEO = False


def score_title_affinity(title):
    title = (title or "").lower().strip()

    if any(word in title for word in _FILTER_CONFIG["too_junior_words"]):
        return {
            "passed": False,
            "score": 0,
            "reason": "title_reject:too_junior",
            "bucket": "reject",
        }

    if any(word in title for word in _FILTER_CONFIG["too_senior_words"]):
        return {
            "passed": False,
            "score": 0,
            "reason": "title_reject:too_senior",
            "bucket": "reject",
        }
    

    if any(phrase in title for phrase in _FILTER_CONFIG["target_titles"]):
        return {
            "passed": True,
            "score": 10,
            "reason": "title_soft_pass:target_title",
            "bucket": "core_pm",
        }

    if any(phrase in title for phrase in _FILTER_CONFIG["adjacent_titles"]):
        return {
            "passed": True,
            "score": 6,
            "reason": "title_soft_pass:adjacent_title",
            "bucket": "adjacent",
        }

    if any(word in title for word in _FILTER_CONFIG["negative_words"]):
        return {
            "passed": False,
            "score": 0,
            "reason": "title_reject:negative_word",
            "bucket": "reject",
        }

    return {
        "passed": True,
        "score": 3,
        "reason": "title_soft_pass:unknown_title",
        "bucket": "unknown",
    }


def check_comp(comp_min, comp_max):
    min_max = _FILTER_CONFIG["min_acceptable_max_comp"]
    if comp_min is None and comp_max is None:
        return {
            "passed": True,
            "reason": "comp_unknown:missing_comp_range",
            "force_review": True,
        }

    if comp_max is not None and comp_max < min_max:
        return {
            "passed": False,
            "reason": f"comp_reject:max_comp_below_{min_max}",
            "force_review": False,
        }

    return {
        "passed": True,
        "reason": "comp_pass:range_ok_or_open_ended",
        "force_review": False,
    }


def normalize_location_text(text):
    return (text or "").lower().strip()


def split_locations(location_text):
    if not location_text:
        return []

    parts = re.split(_FILTER_CONFIG["location_split_pattern"], location_text)
    cleaned = [part.strip() for part in parts if part.strip()]

    if not cleaned:
        return [location_text.strip()]

    return cleaned


def has_any_term(text, terms):
    return any(term in text for term in terms)


def classify_single_location(location):
    location = normalize_location_text(location)

    if not location:
        return {
            "category": "missing",
            "passed": False,
            "reason": "geo_reject:missing_location",
            "force_review": False,
        }

    is_hybrid = has_any_term(location, _FILTER_CONFIG["hybrid_terms"])

    if has_any_term(location, _FILTER_CONFIG["local_region_terms"]):
        if is_hybrid:
            return {
                "category": "hybrid_local",
                "passed": True,
                "reason": "geo_pass:hybrid_bay_area",
                "force_review": False,
            }
        return {
            "category": "bay_area",
            "passed": True,
            "reason": "geo_pass:bay_area",
            "force_review": False,
        }

    if has_any_term(location, _FILTER_CONFIG["non_local_city_terms"]):
        if is_hybrid:
            return {
                "category": "hybrid_non_local",
                "passed": False,
                "reason": "geo_reject:hybrid_non_local_city",
                "force_review": False,
            }
        return {
            "category": "non_local",
            "passed": False,
            "reason": "geo_reject:non_local_city",
            "force_review": False,
        }

    if has_any_term(location, _FILTER_CONFIG["remote_positive_terms"]):
        if has_any_term(location, _FILTER_CONFIG["remote_restricted_terms"]):
            return {
                "category": "remote_restricted",
                "passed": False,
                "reason": "geo_reject:remote_outside_target_geo",
                "force_review": False,
            }

        if has_any_term(location, _FILTER_CONFIG["remote_broad_pass_terms"]):
            return {
                "category": "remote_ok",
                "passed": True,
                "reason": "geo_pass:remote_us_or_ca",
                "force_review": False,
            }

        return {
            "category": "remote_ambiguous",
            "passed": False,
            "reason": "geo_reject:remote_scope_unclear",
            "force_review": False,
        }

    if has_any_term(location, _FILTER_CONFIG["remote_broad_pass_terms"]):
        return {
            "category": "remote_ok",
            "passed": True,
            "reason": "geo_pass:broad_remote_region",
            "force_review": False,
        }

    if is_hybrid:
        return {
            "category": "hybrid_ambiguous",
            "passed": False,
            "reason": "geo_reject:hybrid_location_unclear",
            "force_review": False,
        }

    return {
        "category": "ambiguous",
        "passed": False,
        "reason": "geo_reject:unknown_location",
        "force_review": False,
    }


def check_geo(location_text):
    location_parts = split_locations(location_text)

    if DEBUG_GEO:
        print("DEBUG check_geo raw location_text =", repr(location_text))
        print("DEBUG check_geo location_parts =", location_parts)

    if not location_parts:
        return {
            "passed": False,
            "reason": "geo_reject:missing_location",
            "category": "missing",
            "force_review": False,
            "details": [],
        }

    results = [classify_single_location(part) for part in location_parts]
    details = [f"{part} -> {res['reason']}" for part, res in zip(location_parts, results)]

    if DEBUG_GEO:
        print("DEBUG check_geo results =", results)

    if any(result["category"] in ["non_local", "hybrid_non_local", "remote_restricted"] for result in results):
        return {
            "passed": False,
            "reason": "geo_reject:explicit_non_local_or_restricted_location",
            "category": "non_local",
            "force_review": False,
            "details": details,
        }

    for preferred_category in ["bay_area", "hybrid_local", "remote_ok"]:
        for result in results:
            if result["category"] == preferred_category and result["passed"]:
                return {
                    "passed": True,
                    "reason": result["reason"],
                    "category": result["category"],
                    "force_review": result["force_review"],
                    "details": details,
                }

    return {
        "passed": False,
        "reason": "geo_reject:no_acceptable_location_option",
        "category": "ambiguous",
        "force_review": False,
        "details": details,
    }


def check_description_geo_exclusions(description_text):
    text = normalize_location_text(description_text)

    for phrase in _FILTER_CONFIG["description_location_reject_phrases"]:
        if phrase in text:
            return {
                "passed": False,
                "reason": f"geo_reject:description_exclusion:{phrase}",
            }

    return {
        "passed": True,
        "reason": "geo_pass:no_description_exclusion",
    }


def fast_filter_title_geo(job, llm_geo_classifier=None):
    title = job.get("title", "")
    location = job.get("location", "")
    description = job.get("description", "")

    details = []

    title_result = score_title_affinity(title)
    details.append(title_result["reason"])
    if not title_result["passed"]:
        return {
            "passed": False,
            "reason": title_result["reason"],
            "details": details,
            "force_review": False,
            "geo_category": None,
            "title_score": 0,
            "title_bucket": "reject",
        }

    geo_result = check_geo(location)
    details.extend(geo_result.get("details", []))
    details.append(geo_result["reason"])

    if not geo_result["passed"]:
        return {
            "passed": False,
            "reason": geo_result["reason"],
            "details": details,
            "force_review": False,
            "geo_category": geo_result["category"],
            "title_score": title_result["score"],
            "title_bucket": title_result["bucket"],
        }

    description_geo_result = check_description_geo_exclusions(description)
    details.append(description_geo_result["reason"])

    if not description_geo_result["passed"]:
        return {
            "passed": False,
            "reason": description_geo_result["reason"],
            "details": details,
            "force_review": False,
            "geo_category": geo_result["category"],
            "title_score": title_result["score"],
            "title_bucket": title_result["bucket"],
        }

    return {
        "passed": True,
        "reason": "title_geo_pass",
        "details": details,
        "force_review": geo_result["force_review"],
        "geo_category": geo_result["category"],
        "title_score": title_result["score"],
        "title_bucket": title_result["bucket"],
    }
