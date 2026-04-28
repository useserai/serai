"""
Unified config loader for Serai.

Reads config.yaml (structured data) and profile.md (prose guidance),
and provides clean APIs to all consumers. Falls back to legacy
candidate_data/candidate_profile.txt if config.yaml doesn't exist.

Setup files:
  .env              — API keys, LLM provider, Notion credentials
  config.yaml       — structured config (target roles, dimensions, filters, discovery)
  resume.md         — candidate resume/CV (long-form)
  profile.md        — optional prose guidance (dimension anchors, disqualifiers, role criteria)
"""

import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.yaml"
PROFILE_FILE = BASE_DIR / "profile.md"
RESUME_FILE = BASE_DIR / "candidate_data" / "resume.md"

# Legacy fallback
LEGACY_PROFILE_FILE = BASE_DIR / "candidate_data" / "candidate_profile.txt"

_config: Optional[Dict[str, Any]] = None
_profile_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal: load and cache
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        _config = _load_yaml(CONFIG_FILE)
    return _config


def _get_profile_text() -> str:
    global _profile_text
    if _profile_text is None:
        _profile_text = _load_text(PROFILE_FILE)
    return _profile_text


def is_new_config() -> bool:
    """True if config.yaml exists (new config system), False for legacy."""
    return CONFIG_FILE.exists()


def reload():
    """Force reload of config and profile (useful after edits)."""
    global _config, _profile_text
    _config = None
    _profile_text = None


# ---------------------------------------------------------------------------
# Public API: candidate identity
# ---------------------------------------------------------------------------

def get_candidate_name() -> str:
    cfg = _get_config()
    return cfg.get("candidate", {}).get("name", "")


def get_candidate_summary() -> str:
    cfg = _get_config()
    return cfg.get("candidate", {}).get("summary", "")


def get_candidate_strengths() -> List[str]:
    cfg = _get_config()
    return cfg.get("candidate", {}).get("strengths", [])


# ---------------------------------------------------------------------------
# Public API: target roles
# ---------------------------------------------------------------------------

def get_target_titles() -> List[str]:
    cfg = _get_config()
    return cfg.get("target_roles", {}).get("titles", [])


def get_archetypes() -> List[Dict[str, Any]]:
    cfg = _get_config()
    return cfg.get("target_roles", {}).get("archetypes", [])


def get_adjacent_accepted() -> List[str]:
    cfg = _get_config()
    return cfg.get("target_roles", {}).get("adjacent_accepted", [])


# ---------------------------------------------------------------------------
# Public API: company preferences
# ---------------------------------------------------------------------------

def get_dimension_weights() -> Dict[str, float]:
    cfg = _get_config()
    dims = cfg.get("company_preferences", {}).get("dimensions", {})
    return {name: dim.get("weight", 0.0) for name, dim in dims.items()}


def get_hard_constraints() -> Dict[str, Any]:
    cfg = _get_config()
    return cfg.get("company_preferences", {}).get("hard_constraints", {})


def get_leadership_overrides() -> Dict[str, Dict[str, Any]]:
    """Returns {company_lower: {dimension: str, override_to: int, reason: str}}."""
    cfg = _get_config()
    overrides = cfg.get("company_preferences", {}).get("leadership_overrides", [])
    result = {}
    for o in overrides:
        company = o.get("company", "").lower().strip()
        if company:
            result[company] = {
                "dimension": o.get("dimension", ""),
                "override_to": o.get("override_to", 0),
                "reason": o.get("reason", ""),
            }
    return result


def get_disqualifiers() -> List[str]:
    """Returns disqualifier descriptions from profile.md (full prose).
    Falls back to config.yaml short list if profile.md has no disqualifiers section."""
    profile = _get_profile_text()
    if profile:
        section = _extract_md_section(profile, "Disqualifiers")
        if section:
            return _parse_bullet_items(section)
    # Fallback to config.yaml
    cfg = _get_config()
    return cfg.get("company_preferences", {}).get("disqualifiers", [])


def get_anchor_map() -> Dict[str, Dict[str, Tuple[int, int]]]:
    """Parse dimension anchor companies from profile.md.
    Returns {dimension: {company_lower: (band_low, band_high)}}."""
    profile = _get_profile_text()
    if not profile:
        return {}

    result = {}
    dims = _get_config().get("company_preferences", {}).get("dimensions", {})

    for dim_name in dims:
        section = _extract_md_section(profile, dim_name)
        if not section:
            continue

        anchors = {}
        for match in re.finditer(r"-\s*(\d+)-(\d+):\s*(.+)", section):
            low, high = int(match.group(1)), int(match.group(2))
            text = match.group(3)
            # Extract company names from parentheses and comma-separated items
            for company in re.split(r",\s*", text):
                company = company.strip()
                # Remove parenthetical context
                company = re.sub(r"\s*\(.*?\)\s*", "", company).strip()
                if company and len(company) > 1:
                    anchors[company.lower()] = (low, high)

        if anchors:
            result[dim_name] = anchors

    return result


# ---------------------------------------------------------------------------
# Public API: discovery config
# ---------------------------------------------------------------------------

def get_discovery_config() -> Dict[str, Any]:
    """Returns discovery patterns, adjacent keywords, and broad sweep titles."""
    cfg = _get_config()
    discovery = cfg.get("discovery", {})

    title_patterns = discovery.get("title_patterns", [])
    adjacent = discovery.get("adjacent_title_keywords", [])
    broad_sweep = discovery.get("broad_sweep_titles", [])

    # Build DISCOVERY_PATTERNS from title_patterns if provided
    # Group by archetype signals if archetypes exist, otherwise one flat group
    patterns = []
    if title_patterns:
        patterns = [{"name": "user_defined", "titles": title_patterns}]

    return {
        "patterns": patterns,
        "adjacent_title_keywords": adjacent,
        "broad_sweep_titles": broad_sweep,
    }


# ---------------------------------------------------------------------------
# Public API: filter config
# ---------------------------------------------------------------------------

def get_filter_config() -> Dict[str, Any]:
    """Returns filter config matching the shape of DEFAULT_JOB_FILTER."""
    cfg = _get_config()
    filters = cfg.get("filters", {})
    title = filters.get("title", {})
    comp = filters.get("compensation", {})
    geo = filters.get("geo", {})

    return {
        "target_titles": title.get("target", []),
        "adjacent_titles": title.get("adjacent", []),
        "too_junior_words": title.get("too_junior", []),
        "too_senior_words": title.get("too_senior", []),
        "negative_words": title.get("negative", []),
        "local_region_terms": geo.get("local_region_terms", []),
        "remote_positive_terms": geo.get("remote_positive_terms", []),
        "remote_broad_pass_terms": geo.get("remote_broad_pass_terms", []),
        "remote_restricted_terms": geo.get("remote_restricted_terms", []),
        "non_local_city_terms": geo.get("non_local_city_terms", []),
        "hybrid_terms": geo.get("hybrid_terms", []),
        "description_location_reject_phrases": geo.get("description_location_reject_phrases", []),
        "location_split_pattern": geo.get("location_split_pattern", r"[;/|]|\s+\|\s+|\s+or\s+"),
        "min_acceptable_max_comp": comp.get("min_acceptable_max", 0),
        "unknown_bucket_exclude": filters.get("unknown_bucket_exclude", []),
    }


# ---------------------------------------------------------------------------
# Public API: unknown bucket title substrings
# ---------------------------------------------------------------------------

def get_unknown_bucket_substrings() -> List[str]:
    cfg = _get_config()
    return cfg.get("filters", {}).get("unknown_bucket_exclude", [])


# ---------------------------------------------------------------------------
# Public API: assembled prompt (regression-safe)
# ---------------------------------------------------------------------------

def get_candidate_prompt() -> str:
    """Assemble config.yaml + profile.md into a text block that matches
    the format of the legacy candidate_profile.txt. The LLM sees the same
    sections in the same order."""

    if not is_new_config():
        # Legacy fallback: read candidate_profile.txt directly
        return _load_text(LEGACY_PROFILE_FILE)

    cfg = _get_config()
    profile = _get_profile_text()
    candidate = cfg.get("candidate", {})
    target = cfg.get("target_roles", {})
    prefs = cfg.get("company_preferences", {})
    dims = prefs.get("dimensions", {})

    sections = []

    # --- CANDIDATE SUMMARY ---
    summary = candidate.get("summary", "")
    strengths = candidate.get("strengths", [])
    strengths_text = ", ".join(strengths) if strengths else ""
    section = f"CANDIDATE SUMMARY\n \n{summary}"
    if strengths_text:
        section += f" Strongest areas: {strengths_text}."
    sections.append(section)

    # --- COMPANY EVALUATION: ARCHETYPE ---
    titles = target.get("titles", [])
    archetypes = target.get("archetypes", [])
    adjacent = target.get("adjacent_accepted", [])
    attractive = prefs.get("attractive_signals", [])

    archetype_names = [a.get("name", "") for a in archetypes]
    primary = ", ".join(archetype_names) if archetype_names else ""
    adj_text = ", ".join(adjacent) if adjacent else ""
    attr_text = "; ".join(attractive) if attractive else ""

    section = f"COMPANY EVALUATION: ARCHETYPE\n \n"
    section += f"Target roles: {', '.join(titles)}."
    if primary:
        section += f" Primary archetypes: {primary}."
    if adj_text:
        section += f" Secondary/adjacent: {adj_text}."
    if attr_text:
        section += f" Best-fit companies: {attr_text}."
    sections.append(section)

    # --- COMPANY EVALUATION: DIMENSIONS AND WEIGHTS ---
    dim_lines = ["COMPANY EVALUATION: DIMENSIONS AND WEIGHTS (sum to 1.0)\n"]
    for dim_name, dim_cfg in dims.items():
        weight = dim_cfg.get("weight", 0.0)
        dim_lines.append(f"{dim_name} (weight: {weight})")

        # Pull prose guidance from profile.md
        guidance = _extract_md_section(profile, dim_name) if profile else ""
        if guidance:
            # Indent guidance lines
            for line in guidance.strip().splitlines():
                dim_lines.append(f"  {line}")
        else:
            desc = dim_cfg.get("description", "")
            if desc:
                dim_lines.append(f"  {desc}")
        dim_lines.append("")

    sections.append("\n".join(dim_lines).strip())

    # --- HARD CONSTRAINTS ---
    hc = prefs.get("hard_constraints", {})
    hc_lines = ["COMPANY EVALUATION: HARD CONSTRAINTS\n"]
    for key, value in hc.items():
        if isinstance(value, list):
            hc_lines.append(f"{key.replace('_', ' ').title()}: {', '.join(str(v) for v in value)}")
        else:
            hc_lines.append(f"{key.replace('_', ' ').title()}: {value}")
    sections.append("\n".join(hc_lines))

    # --- DISQUALIFIERS ---
    disqualifiers = get_disqualifiers()
    if disqualifiers:
        dq_lines = [
            "COMPANY EVALUATION: CANDIDATE-SPECIFIC DISQUALIFIERS\n",
            "These are routing overrides — the company may score well on dimensions but",
            "is wrong for this candidate. Flag if matched; route to Skip post-scoring.",
        ]
        for dq in disqualifiers:
            dq_lines.append(f"- {dq}")
        sections.append("\n".join(dq_lines))

    # --- LEADERSHIP OVERRIDES ---
    overrides = prefs.get("leadership_overrides", [])
    if overrides:
        lo_lines = [
            "COMPANY EVALUATION: LEADERSHIP OVERRIDES\n",
            "Insider knowledge the LLM cannot have. Apply after LLM scoring, before",
            "computing weighted sum.",
        ]
        for o in overrides:
            lo_lines.append(
                f"- {o.get('company', '')}: {o.get('dimension', '')} override to "
                f"{o.get('override_to', '')}. {o.get('reason', '')}"
            )
        sections.append("\n".join(lo_lines))

    # --- WHAT MAKES A COMPANY ATTRACTIVE ---
    if attractive:
        attr_lines = ["COMPANY EVALUATION: WHAT MAKES A COMPANY ESPECIALLY ATTRACTIVE\n"]
        # Pull from profile.md if available
        attr_section = _extract_md_section(profile, "What makes a company attractive") if profile else ""
        if attr_section:
            attr_lines.append(attr_section.strip())
        else:
            for a in attractive:
                attr_lines.append(f"- {a}")
        sections.append("\n".join(attr_lines))

    # --- ROLE EVALUATION: WHAT MAKES A ROLE ATTRACTIVE ---
    role_attractive = _extract_md_section(profile, "What makes a role attractive") if profile else ""
    if role_attractive:
        sections.append(f"ROLE EVALUATION: WHAT MAKES A ROLE ATTRACTIVE\n\n{role_attractive.strip()}")

    # --- ROLE EVALUATION: ADJACENT ROLES ACCEPTED ---
    role_adjacent = _extract_md_section(profile, "Adjacent roles accepted") if profile else ""
    if role_adjacent:
        sections.append(f"ROLE EVALUATION: ADJACENT ROLES ACCEPTED\n\n{role_adjacent.strip()}")

    # --- ROLE EVALUATION: WHAT MAKES A ROLE LESS ATTRACTIVE ---
    role_less = _extract_md_section(profile, "What makes a role less attractive") if profile else ""
    if role_less:
        sections.append(f"ROLE EVALUATION: WHAT MAKES A ROLE LESS ATTRACTIVE\n\n{role_less.strip()}")

    # --- EVALUATION INSTRUCTIONS (system-level, always included) ---
    sections.append(
        "EVALUATION INSTRUCTIONS\n \n"
        "For company evaluation: use the dimensions, weights, and anchors above. "
        "Score each dimension independently. Do not let one dimension influence another. "
        "Be conservative — high scores are rare.\n \n"
        "For role evaluation: estimate how likely the candidate is to want to apply "
        "after reading the JD. Be conservative and practical. Do not assume missing "
        "experience. Do not over-reward generic prestige, company size, or brand "
        "recognition. Favor company and role combinations that match the candidate's "
        "actual energy, strengths, and long-term direction."
    )

    return "\n---\n \n".join(sections)


def get_resume_text() -> str:
    """Load resume.md content."""
    return _load_text(RESUME_FILE)

def get_anchor_stories() -> Dict[str, Any]:
    """Load anchor stories from config.yaml or fall back to candidate_data/anchor_stories.yaml."""
    import yaml
    cfg = _get_config()
    stories = cfg.get("anchor_stories", {})
    if stories:
        return stories
    legacy_path = BASE_DIR / "candidate_data" / "anchor_stories.yaml"
    if legacy_path.exists():
        try:
            with legacy_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {"stories": []}
        except (OSError, Exception):
            pass
    return {"stories": []}


def get_keyword_weights() -> Dict[str, int]:
    """Load keyword weights from config.yaml or fall back to candidate_data/keyword_weights.yaml."""
    import yaml
    cfg = _get_config()
    weights = cfg.get("keyword_weights", {})
    if weights:
        return {k.lower().strip().replace("_", " "): int(v) for k, v in weights.items()
                if isinstance(k, str) and k.strip()}
    legacy_path = BASE_DIR / "candidate_data" / "keyword_weights.yaml"
    if legacy_path.exists():
        try:
            with legacy_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                if isinstance(raw, dict):
                    out = {}
                    for k, v in raw.items():
                        if not isinstance(k, str):
                            continue
                        key = k.lower().strip().replace("_", " ")
                        if not key:
                            continue
                        try:
                            out[key] = int(v)
                        except (TypeError, ValueError):
                            continue
                    if out:
                        return out
        except (OSError, Exception):
            pass
    return {}


def get_unknown_bucket_title_substrings_from_profile() -> str:
    """Return the raw candidate profile text for unknown-bucket substring parsing."""
    if is_new_config():
        return get_candidate_prompt()
    return _load_text(LEGACY_PROFILE_FILE)


def get_discovery_keywords() -> tuple:
    """Return (adjacent_keywords, broad_sweep_titles) from config.yaml or legacy profile."""
    cfg = _get_config()
    discovery = cfg.get("discovery", {})
    adjacent = discovery.get("adjacent_title_keywords", [])
    broad = discovery.get("broad_sweep_titles", [])
    if adjacent or broad:
        return adjacent, broad
    from discovery_patterns import parse_discovery_config_from_profile
    profile_text = get_candidate_prompt()
    return parse_discovery_config_from_profile(profile_text)

# ---------------------------------------------------------------------------
# Internal: markdown section extraction
# ---------------------------------------------------------------------------

def _extract_md_section(text: str, heading: str) -> str:
    """Extract content under a ## heading in profile.md until the next ## or EOF."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return ""

    start = match.end()
    # Find next ## heading or end of text
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    if next_heading:
        end = start + next_heading.start()
    else:
        end = len(text)

    return text[start:end].strip()


def _parse_bullet_items(text: str) -> List[str]:
    """Parse markdown bullet items (- item) from a block of text."""
    items = []
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                items.append(current.strip())
            current = stripped[2:]
        elif current and stripped:
            # Continuation line
            current += " " + stripped
    if current:
        items.append(current.strip())
    return items
