import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from llm_client import call_llm, get_model_name

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = Path("llm_company_cache.json")
MODEL_NAME = get_model_name()
COMPANY_PROMPT_VERSION = "company_v15"
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY")
PROMPT_TEMPLATE_FILE = BASE_DIR / "prompts" / "company_eval_prompt.txt"


def load_text_file(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


PROMPT_TEMPLATE = load_text_file(PROMPT_TEMPLATE_FILE)

DEFAULT_DIMENSIONS = [
    "moat_durability",
    "growth_trajectory",
    "product_culture",
    "ai_leverage",
    "leadership_quality",
    "market_category",
]


def load_company_cache() -> Dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_company_cache(cache: Dict[str, Any]) -> None:
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def normalize_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        return value if value else default
    return str(value)


def fetch_recent_signals(company_name: str) -> str:
    """Fetch recent web snippets for the company. Returns string to inject into prompt."""
    if not BRAVE_SEARCH_API_KEY:
        return "No recent signals available."

    query = f"{company_name} news funding layoffs revenue growth 2025 2026"

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": query,
                "count": 5,
                "freshness": "py",
                "search_lang": "en",
                "country": "us",
                "spellcheck": 1,
            },
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            },
            timeout=10,
        )

        if not response.ok:
            return "No recent signals available."

        results = response.json().get("web", {}).get("results", [])
        if not results:
            return "No recent signals available."

        snippets = []
        for result in results[:5]:
            title = normalize_text(result.get("title"))
            description = normalize_text(result.get("description"))
            if title or description:
                snippets.append(f"- {title}: {description}".strip(": "))

        if not snippets:
            return "No recent signals available."

        return "\n".join(snippets)

    except Exception:
        return "No recent signals available."


def make_signals_hash(recent_signals: str) -> str:
    return hashlib.md5(normalize_text(recent_signals).encode("utf-8")).hexdigest()


def make_company_cache_key(company_name: str, candidate_profile: str, signals_hash: str) -> str:
    key_payload = {
        "company_prompt_version": COMPANY_PROMPT_VERSION,
        "company_name": normalize_text(company_name).lower(),
        "candidate_profile": normalize_text(candidate_profile),
        "signals_hash": signals_hash,
    }
    key_str = json.dumps(key_payload, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()


def build_prompt(company_name: str, candidate_profile: str, recent_signals: str) -> str:
    if not PROMPT_TEMPLATE:
        raise ValueError(
            f"Prompt template file is missing or empty: {PROMPT_TEMPLATE_FILE}"
        )

    return PROMPT_TEMPLATE.format(
        candidate_profile=normalize_text(candidate_profile, "No candidate profile provided."),
        company_name=normalize_text(company_name, "Unknown company"),
        recent_signals=normalize_text(recent_signals, "No recent signals available."),
    )


def get_json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "company_interest_score": {"type": ["number", "null"]},
            "dimension_scores": {
                "type": "object",
                "properties": {
                    "moat_durability": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                    "growth_trajectory": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                    "product_culture": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                    "ai_leverage": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                    "leadership_quality": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                    "market_category": {
                        "type": "object",
                        "properties": {"score": {"type": "number"}},
                        "required": ["score"],
                        "additionalProperties": False,
                    },
                },
                "required": DEFAULT_DIMENSIONS,
                "additionalProperties": False,
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "company_interest": {"type": ["string", "null"]},
            "why_company": {
                "type": "array",
                "items": {"type": "string"},
            },
            "why_not_company": {
                "type": "array",
                "items": {"type": "string"},
            },
            "company_interest_reason": {"type": "string"},
            "disqualifier_flags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "company_interest_score",
            "dimension_scores",
            "confidence",
            "company_interest",
            "why_company",
            "why_not_company",
            "company_interest_reason",
            "disqualifier_flags",
        ],
        "additionalProperties": False,
    }


def fallback_result() -> Dict[str, Any]:
    return {
        "company_interest_score": None,
        "dimension_scores": {
            dim: {"score": 5.0} for dim in DEFAULT_DIMENSIONS
        },
        "confidence": "low",
        "company_interest": None,
        "why_company": [],
        "why_not_company": ["Failed to parse LLM output"],
        "company_interest_reason": "Failed to parse LLM output",
        "disqualifier_flags": [],
    }


def parse_dimension_weights(profile_text: str) -> Dict[str, float]:
    """Extract dimension weights from candidate profile."""
    weights = {}
    for match in re.finditer(r'(\w+)\s*\(weight:\s*([\d.]+)\)', profile_text):
        weights[match.group(1)] = float(match.group(2))

    if not weights:
        equal_weight = round(1.0 / len(DEFAULT_DIMENSIONS), 4)
        return {dim: equal_weight for dim in DEFAULT_DIMENSIONS}

    return weights


def parse_leadership_overrides(profile_text: str) -> Dict[str, Dict[str, int]]:
    """Extract leadership overrides from candidate profile."""
    overrides = {}
    override_section = re.search(
        r'LEADERSHIP OVERRIDES.*?(?=\n\n|\nCOMPANY|\nROLE|\Z)',
        profile_text,
        re.DOTALL,
    )
    if override_section:
        for match in re.finditer(
            r'(\w+):\s*(\w+)\s*override\s*to\s*(\d+)',
            override_section.group(),
        ):
            company = match.group(1).lower()
            dim = match.group(2)
            score = int(match.group(3))
            if company not in overrides:
                overrides[company] = {}
            overrides[company][dim] = score
    return overrides


def parse_candidate_disqualifiers(profile_text: str) -> List[str]:
    """Very simple parser for disqualifier bullets/lines."""
    match = re.search(
        r'DISQUALIFIERS.*?(?=\n\n|\nCOMPANY|\nROLE|\Z)',
        profile_text,
        re.DOTALL,
    )
    if not match:
        return []

    block = match.group()
    items = []

    for line in block.splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line or line.upper().startswith("DISQUALIFIERS"):
            continue
        items.append(line.lower())

    return items
def parse_anchor_map(profile_text: str) -> dict:
    """
    Parse candidate profile to extract anchor company → band mappings.
    Returns: {dimension: {company_name_lower: (band_low, band_high)}}
    """
    anchor_map = {}
    current_dim = None
    in_anchors = False
    current_band = None

    for line in profile_text.split('\n'):
        stripped = line.strip()

        #Exit anchors mode on blank line
        if not stripped:
            in_anchors = False
            current_band = None
            continue

        dim_match = re.match(r'^(\w+)\s*\(weight:\s*[\d.]+\)', stripped)
        if dim_match:
            current_dim = dim_match.group(1)
            anchor_map[current_dim] = {}
            in_anchors = False
            current_band = None
            continue

        if stripped == 'Anchors:':
            in_anchors = True
            continue

        if not in_anchors or not current_dim:
            continue

        band_match = re.match(r'^(\d+)-(\d+):\s*(.+)', stripped)
        if band_match:
            current_band = (int(band_match.group(1)), int(band_match.group(2)))
            text = band_match.group(3)
        elif current_band and stripped:
            text = stripped
        else:
            continue

        for paren in re.findall(r'\(([^)]+)\)', text):
            for name in paren.split(','):
                name = name.strip()
                clean = re.sub(r'\s+\d{4}.*$', '', name).strip()
                clean = re.sub(r'\s*/.+$', '', clean).strip()
                if clean and clean[0].isupper() and len(clean.split()) <= 3 and not any(w.islower() for w in clean.split()):
                    anchor_map[current_dim][clean.lower()] = current_band

        for part in text.split(','):
            part = part.strip()
            if not part or not part[0].isupper():
                continue
            clean = re.sub(r'\s+\d{4}[-–]\d{4}.*$', '', part).strip()
            clean = re.sub(r'\s*\(.*\)$', '', clean).strip()
            words = clean.split()
            if len(words) <= 3 and all(w[0].isupper() for w in words if w):
                anchor_map[current_dim][clean.lower()] = current_band

    return anchor_map


def apply_anchor_overrides(company_name: str, dim_scores: dict, anchor_map: dict) -> dict:
    """
    If company is a named anchor for a dimension, clamp score to the anchor band.
    dim_scores: {dimension_name: {"score": number}} (nested structure from LLM)
    """
    company_lower = company_name.lower().strip()

    for dim, data in dim_scores.items():
        if dim not in anchor_map:
            continue
        if company_lower not in anchor_map[dim]:
            continue

        band_low, band_high = anchor_map[dim][company_lower]
        current = data["score"]

        if current < band_low:
            print(f"  Anchor override {company_name}/{dim}: {current} → {band_low}")
            data["score"] = band_low
        elif current > band_high:
            print(f"  Anchor override {company_name}/{dim}: {current} → {band_high}")
            data["score"] = band_high

    return dim_scores


def compute_weighted_score(dim_scores: dict, weights: dict, overrides: dict = None) -> float:
    """Compute weighted company score from LLM dimension scores."""
    scores = {}

    for dim, data in dim_scores.items():
        score = data["score"]
        if overrides and dim in overrides:
            score = overrides[dim]
        scores[dim] = score

    total_weight = sum(weights.values()) if weights else 0.0
    if total_weight <= 0:
        equal_weight = 1.0 / len(scores)
        weighted = sum(score * equal_weight for score in scores.values())
    else:
        weighted = sum(scores[dim] * weights.get(dim, 0.0) for dim in scores) / total_weight

    return round(weighted, 2)


def score_to_interest_band(score: float) -> str:
    """Derive interest band from computed score."""
    if score >= 7.5:
        return "strong"
    elif score >= 6.0:
        return "moderate"
    else:
        return "weak"


def check_disqualifiers(
    company_name: str,
    company_notes: str,
    candidate_disqualifiers: list,
    llm_flags: list,
    dim_scores: dict = None,
) -> list:
    """Merge LLM-flagged and code-detected disqualifiers, with score gating."""

    # Named examples from candidate profile disqualifiers
    known_consumer = {"roblox", "snap", "epic games"}
    known_enterprise = {"oracle", "sap", "salesforce"}

    company_lower = normalize_text(company_name).lower()
    haystack = f"{normalize_text(company_name)} {normalize_text(company_notes)}".lower()

    #--- Code detection (proactive keyword + name matching)---
    code_flags = set()
    if company_lower in known_consumer:
        code_flags.add("consumer social / gaming")
    else:
        for phrase in ["b2c gaming", "consumer gaming", "consumer social", "social gaming", "metaverse"]:
            if phrase in haystack:
                code_flags.add("consumer social / gaming")
                break
    # Legacy enterprise: named examples always match
    if company_lower in known_enterprise:
        code_flags.add("legacy enterprise sales org")

    # Hardware: strict phrases only
    for phrase in ["hardware manufacturer", "chip manufacturer", "semiconductor"]:
        if phrase in haystack:
            code_flags.add("hardware")
            break

    # --- LLM flag filtering (remove false positives) ---
    filtered_llm_flags = set()
    for flag in llm_flags:
        flag_clean = normalize_text(flag).lower()
        if not flag_clean:
            continue

        # Gate: enterprise requires known name OR enterprise keywords, AND pc <= 4
        if "enterprise" in flag_clean:
            is_known = company_lower in known_enterprise
            has_keywords = any(kw in haystack for kw in ["erp", "enterprise software", "database for enterprise"])
            if (is_known or has_keywords) and dim_scores:
                pc = dim_scores.get("product_culture", {}).get("score", 5)
                if pc <= 4:
                    filtered_llm_flags.add("legacy enterprise sales org")
            continue

        # Gate: consumer requires known name OR gaming/social keywords in signals
        if "consumer" in flag_clean and ("social" in flag_clean or "gaming" in flag_clean):
            is_known = company_lower in known_consumer
            has_keywords = any(kw in haystack for kw in ["gaming", "video game", "consumer social", "social network"])
            if is_known or has_keywords:
                filtered_llm_flags.add("consumer social / gaming")
            continue

        # Gate: hardware requires strict keyword evidence (blocks "Tom's Hardware" etc.)
        if "hardware" in flag_clean:
            if any(kw in haystack for kw in ["hardware manufacturer", "chip", "semiconductor", "manufactures hardware"]):
                filtered_llm_flags.add("hardware")
            continue

        filtered_llm_flags.add(flag_clean)

    return sorted(code_flags | filtered_llm_flags)


def llm_score_company(company_name: str, candidate_profile: str) -> Dict[str, Any]:
    recent_signals = fetch_recent_signals(company_name)
    signals_hash = make_signals_hash(recent_signals)

    cache = load_company_cache()
    cache_key = make_company_cache_key(company_name, candidate_profile, signals_hash)

    if cache_key in cache:
        return cache[cache_key]

    prompt = build_prompt(company_name, candidate_profile, recent_signals)

    content = call_llm(prompt, get_json_schema(), "company_interest_score")

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = fallback_result()

    dim_scores = result.get("dimension_scores", {})
    anchor_map = parse_anchor_map(candidate_profile)
    dim_scores = apply_anchor_overrides(company_name, dim_scores, anchor_map)
    weights = parse_dimension_weights(candidate_profile)
    all_overrides = parse_leadership_overrides(candidate_profile)
    company_overrides = all_overrides.get(normalize_text(company_name).lower(), {})
    candidate_disqualifiers = parse_candidate_disqualifiers(candidate_profile)

    result["company_interest_score"] = compute_weighted_score(dim_scores, weights, company_overrides)
    result["company_interest"] = score_to_interest_band(result["company_interest_score"])

    disqualifiers = check_disqualifiers(
        company_name=company_name,
        company_notes=recent_signals,
        candidate_disqualifiers=candidate_disqualifiers,
        llm_flags=result.get("disqualifier_flags", []),
        dim_scores=dim_scores,
    )
    result["disqualifier_flags"] = disqualifiers

    result["company_reasoning_summary"] = result["company_interest_reason"]
    result["company_prompt_version"] = COMPANY_PROMPT_VERSION
    result["recent_signals"] = recent_signals
    result["signals_hash"] = signals_hash

    cache[cache_key] = result
    save_company_cache(cache)

    return result
