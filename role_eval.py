"""
Two-stage role evaluation pipeline.

Stage 1 (screen): Blocks A+B+C — archetype, resume match, level fit.
  Runs on all pre-filtered roles. Cheap.
Stage 2 (deep eval): Blocks D+E+F+G — comp, resume personalization, interview prep, legitimacy.
  Runs only on Apply / Apply with Caution roles. Expensive.

Consumers call:
  stage1_screen(job, candidate_profile, company_result) -> dict
  stage2_deep_eval(job, candidate_profile, company_result, stage1_result) -> dict
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_client import call_llm, get_model_name
import config_loader


BASE_DIR = Path(__file__).resolve().parent
STAGE1_PROMPT_FILE = BASE_DIR / "prompts" / "stage1_screen_prompt.txt"
STAGE2_PROMPT_FILE = BASE_DIR / "prompts" / "stage2_deep_eval_prompt.txt"
STAGE1_CACHE_FILE = BASE_DIR / "stage1_cache.json"
STAGE2_CACHE_FILE = BASE_DIR / "stage2_cache.json"
REPORTS_DIR = BASE_DIR / "reports"

STAGE1_VERSION = "stage1_v2"
STAGE2_VERSION = "stage2_v2"
MODEL_NAME = get_model_name()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _normalize(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        return value if value else default
    return str(value)


def _extract_job_description(job: Dict[str, Any]) -> str:
    for key in ("description", "job_description", "content", "body", "text",
                "requirements", "responsibilities", "summary", "details"):
        val = _normalize(job.get(key), "")
        if val:
            return val
    return "No job description provided."


def _load_cache(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(path: Path, cache: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _format_archetypes(archetypes: List[Dict[str, Any]]) -> str:
    """Format archetypes from config.yaml into a readable block for the prompt."""
    if not archetypes:
        return "No target archetypes defined."
    lines = []
    for arch in archetypes:
        name = arch.get("name", "Unknown")
        signals = arch.get("signals", [])
        lines.append(f"- {name}: {', '.join(signals)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Anchor stories (optional enrichment)
# ---------------------------------------------------------------------------

def _load_anchor_stories() -> Dict[str, Any]:
    """Load anchor stories via config_loader (supports config.yaml and legacy path)."""
    return config_loader.get_anchor_stories()


def _format_anchor_stories(stories: List[Dict[str, Any]]) -> str:
    if not stories:
        return "No anchor stories available. Use resume experience for interview preparation."
    chunks = []
    for story in stories[:3]:
        title = _normalize(story.get("title"), "Untitled")
        theme = _normalize(story.get("theme"), "N/A")
        strengths = ", ".join(s for s in story.get("strengths", []) if isinstance(s, str)) or "N/A"
        summary = _normalize(story.get("summary"), "N/A")
        chunks.append(
            f"- {title}\n"
            f"  Theme: {theme}\n"
            f"  Strengths: {strengths}\n"
            f"  Summary: {summary}"
        )
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Stage 1: Screen (Blocks A + B + C)
# ---------------------------------------------------------------------------

STAGE1_TEMPLATE = _load_text(STAGE1_PROMPT_FILE)


def _stage1_cache_key(job: Dict[str, Any], candidate_profile: str,
                      company_result: Dict[str, Any]) -> str:
    payload = {
        "version": STAGE1_VERSION,
        "model": MODEL_NAME,
        "profile_hash": _md5(candidate_profile),
        "resume_hash": _md5(config_loader.get_resume_text()),
        "template_hash": _md5(STAGE1_TEMPLATE),
        "company": _normalize(job.get("company")),
        "title": _normalize(job.get("title")),
        "url": _normalize(job.get("url")),
        "location": _normalize(job.get("location")),
        "compensation": _normalize(job.get("compensation")),
        "jd": _extract_job_description(job),
        "company_score": company_result.get("company_interest_score"),
    }
    return _md5(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _stage1_json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "company_interest_score": {"type": "number"},
            "archetype": {"type": "string"},
            "archetype_secondary": {"type": ["string", "null"]},
            "archetype_confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "domain": {"type": "string"},
            "function": {
                "type": "string",
                "enum": ["build", "consult", "manage", "deploy"],
            },
            "seniority_detected": {"type": "string"},
            "resume_match_score": {"type": "number"},
            "resume_match_top_strengths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "resume_gaps": {
                "type": "array",
                "items": {"type": "string"},
            },
            "gap_severity": {
                "type": "string",
                "enum": ["none", "manageable", "significant", "blocking"],
            },
            "level_fit_score": {"type": "number"},
            "level_assessment": {"type": "string"},
            "screen_score": {"type": "number"},
            "screen_route": {
                "type": "string",
                "enum": ["Apply", "Apply with Caution", "Skip"],
            },
            "differentiation_reason": {"type": "string"},
            "main_reservation": {"type": "string"},
        },
        "required": [
            "company_interest_score",
            "archetype",
            "archetype_secondary",
            "archetype_confidence",
            "domain",
            "function",
            "seniority_detected",
            "resume_match_score",
            "resume_match_top_strengths",
            "resume_gaps",
            "gap_severity",
            "level_fit_score",
            "level_assessment",
            "screen_score",
            "screen_route",
            "differentiation_reason",
            "main_reservation",
        ],
        "additionalProperties": False,
    }


def _stage1_fallback(company_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_interest_score": company_result.get("company_interest_score", 5.0),
        "archetype": "Other",
        "archetype_secondary": None,
        "archetype_confidence": "low",
        "domain": "unknown",
        "function": "build",
        "seniority_detected": "unknown",
        "resume_match_score": 2.5,
        "resume_match_top_strengths": [],
        "resume_gaps": ["Failed to parse LLM output"],
        "gap_severity": "significant",
        "level_fit_score": 2.5,
        "level_assessment": "Could not assess",
        "screen_score": 2.5,
        "screen_route": "Skip",
        "differentiation_reason": "",
        "main_reservation": "Failed to parse LLM output",
    }


def stage1_screen(
    job: Dict[str, Any],
    candidate_profile: str,
    company_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Run Stage 1 screen. Returns structured result dict."""

    if not STAGE1_TEMPLATE:
        raise ValueError(f"Stage 1 prompt template missing: {STAGE1_PROMPT_FILE}")

    cache = _load_cache(STAGE1_CACHE_FILE)
    cache_key = _stage1_cache_key(job, candidate_profile, company_result)

    if cache_key in cache:
        print(f"  [stage1 cache hit] {_normalize(job.get('company'))} | {_normalize(job.get('title'))}")
        return cache[cache_key]

    # Build context
    jd = _extract_job_description(job)
    anchor_data = _load_anchor_stories()
    stories = anchor_data.get("stories", [])
    anchor_text = _format_anchor_stories(stories[:3])

    archetypes = config_loader.get_archetypes()
    archetypes_text = _format_archetypes(archetypes)

    background = company_result.get(
        "company_interest_reason",
        company_result.get("company_interest", "No company context provided."),
    )

    prompt = STAGE1_TEMPLATE.format(
        candidate_profile=_normalize(candidate_profile, "No candidate profile provided."),
        resume_text=_normalize(config_loader.get_resume_text(), "No resume provided."),
        anchor_stories=anchor_text,
        archetypes=archetypes_text,
        background_company_context=_normalize(background, "No company context provided."),
        company_interest_score=company_result.get("company_interest_score", "N/A"),
        company=_normalize(job.get("company")),
        job_title=_normalize(job.get("title")),
        location=_normalize(job.get("location")),
        compensation=_normalize(job.get("compensation")),
        job_description=jd,
    )

    print(f"  [stage1] {_normalize(job.get('company'))} | {_normalize(job.get('title'))}")

    content = call_llm(prompt, _stage1_json_schema(), "stage1_screen")

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = _stage1_fallback(company_result)

    result["stage1_version"] = STAGE1_VERSION

    cache[cache_key] = result
    _save_cache(STAGE1_CACHE_FILE, cache)

    return result


# ---------------------------------------------------------------------------
# Stage 2: Deep Eval (Blocks D + E + F + G)
# ---------------------------------------------------------------------------

STAGE2_TEMPLATE = _load_text(STAGE2_PROMPT_FILE)


def _stage2_cache_key(job: Dict[str, Any], candidate_profile: str,
                      company_result: Dict[str, Any],
                      stage1_result: Dict[str, Any]) -> str:
    payload = {
        "version": STAGE2_VERSION,
        "model": MODEL_NAME,
        "profile_hash": _md5(candidate_profile),
        "resume_hash": _md5(config_loader.get_resume_text()),
        "template_hash": _md5(STAGE2_TEMPLATE),
        "company": _normalize(job.get("company")),
        "title": _normalize(job.get("title")),
        "url": _normalize(job.get("url")),
        "jd": _extract_job_description(job),
        "company_score": company_result.get("company_interest_score"),
        "stage1_hash": _md5(json.dumps(stage1_result, sort_keys=True, ensure_ascii=False)),
    }
    return _md5(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _stage2_json_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "comp_assessment": {
                "type": "string",
                "enum": ["above_market", "at_market", "below_market", "unknown"],
            },
            "comp_estimate_low": {"type": ["number", "null"]},
            "comp_estimate_high": {"type": ["number", "null"]},
            "comp_notes": {"type": "string"},
            "role_demand": {
                "type": "string",
                "enum": ["high_demand", "moderate_demand", "low_demand"],
            },
            "hiring_context": {"type": "string"},
            "resume_changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string"},
                        "current": {"type": "string"},
                        "proposed": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["section", "current", "proposed", "why"],
                    "additionalProperties": False,
                },
            },
            "positioning_summary": {"type": "string"},
            "interview_stories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {"type": "string"},
                        "experience_match": {"type": "string"},
                        "star_skeleton": {
                            "type": "object",
                            "properties": {
                                "situation": {"type": "string"},
                                "task": {"type": "string"},
                                "action": {"type": "string"},
                                "result": {"type": "string"},
                                "reflection": {"type": "string"},
                            },
                            "required": ["situation", "task", "action", "result", "reflection"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["requirement", "experience_match", "star_skeleton"],
                    "additionalProperties": False,
                },
            },
            "red_flag_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "approach": {"type": "string"},
                    },
                    "required": ["question", "approach"],
                    "additionalProperties": False,
                },
            },
            "case_study_recommendation": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["project", "why"],
                "additionalProperties": False,
            },
            "legitimacy_tier": {
                "type": "string",
                "enum": ["High Confidence", "Proceed with Caution", "Suspicious"],
            },
            "legitimacy_signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signal": {"type": "string"},
                        "finding": {"type": "string"},
                        "weight": {
                            "type": "string",
                            "enum": ["positive", "neutral", "concerning"],
                        },
                    },
                    "required": ["signal", "finding", "weight"],
                    "additionalProperties": False,
                },
            },
            "legitimacy_notes": {"type": "string"},
            "deep_eval_score": {"type": "number"},
            "final_route": {
                "type": "string",
                "enum": ["Strong Apply", "Apply", "Do Not Apply"],
            },
            "apply_urgency": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "one_line_summary": {"type": "string"},
        },
        "required": [
            "comp_assessment",
            "comp_estimate_low",
            "comp_estimate_high",
            "comp_notes",
            "role_demand",
            "hiring_context",
            "resume_changes",
            "positioning_summary",
            "interview_stories",
            "red_flag_questions",
            "case_study_recommendation",
            "legitimacy_tier",
            "legitimacy_signals",
            "legitimacy_notes",
            "deep_eval_score",
            "final_route",
            "apply_urgency",
            "one_line_summary",
        ],
        "additionalProperties": False,
    }


def _stage2_fallback() -> Dict[str, Any]:
    return {
        "comp_assessment": "unknown",
        "comp_estimate_low": None,
        "comp_estimate_high": None,
        "comp_notes": "Failed to parse LLM output",
        "role_demand": "moderate_demand",
        "hiring_context": "Unknown",
        "resume_changes": [],
        "positioning_summary": "",
        "interview_stories": [],
        "red_flag_questions": [],
        "case_study_recommendation": {"project": "", "why": ""},
        "legitimacy_tier": "Proceed with Caution",
        "legitimacy_signals": [],
        "legitimacy_notes": "Failed to parse LLM output",
        "deep_eval_score": 3.0,
        "final_route": "Do Not Apply",
        "apply_urgency": "low",
        "one_line_summary": "Evaluation failed — review manually.",
    }


def stage2_deep_eval(
    job: Dict[str, Any],
    candidate_profile: str,
    company_result: Dict[str, Any],
    stage1_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Run Stage 2 deep eval. Returns structured result dict + saves report."""

    if not STAGE2_TEMPLATE:
        raise ValueError(f"Stage 2 prompt template missing: {STAGE2_PROMPT_FILE}")

    cache = _load_cache(STAGE2_CACHE_FILE)
    cache_key = _stage2_cache_key(job, candidate_profile, company_result, stage1_result)

    if cache_key in cache:
        print(f"  [stage2 cache hit] {_normalize(job.get('company'))} | {_normalize(job.get('title'))}")
        return cache[cache_key]

    # Build context
    jd = _extract_job_description(job)
    anchor_data = _load_anchor_stories()
    stories = anchor_data.get("stories", [])
    anchor_text = _format_anchor_stories(stories[:3])

    archetypes = config_loader.get_archetypes()
    archetypes_text = _format_archetypes(archetypes)

    background = company_result.get(
        "company_interest_reason",
        company_result.get("company_interest", "No company context provided."),
    )

    recent_signals = company_result.get("recent_signals", "No recent signals available.")

    stage1_json = json.dumps(stage1_result, indent=2, ensure_ascii=False)

    prompt = STAGE2_TEMPLATE.format(
        stage1_results=stage1_json,
        candidate_profile=_normalize(candidate_profile, "No candidate profile provided."),
        resume_text=_normalize(config_loader.get_resume_text(), "No resume provided."),
        anchor_stories=anchor_text,
        archetypes=archetypes_text,
        company=_normalize(job.get("company")),
        job_title=_normalize(job.get("title")),
        location=_normalize(job.get("location")),
        compensation=_normalize(job.get("compensation")),
        job_description=jd,
        background_company_context=_normalize(background, "No company context provided."),
        company_interest_score=company_result.get("company_interest_score", "N/A"),
        recent_signals=_normalize(recent_signals, "No recent signals available."),
    )

    print(f"  [stage2] {_normalize(job.get('company'))} | {_normalize(job.get('title'))}")

    content = call_llm(prompt, _stage2_json_schema(), "stage2_deep_eval")

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = _stage2_fallback()

    result["stage2_version"] = STAGE2_VERSION

    # Save report to disk
    _save_report(job, company_result, stage1_result, result)

    cache[cache_key] = result
    _save_cache(STAGE2_CACHE_FILE, cache)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _save_report(
    job: Dict[str, Any],
    company_result: Dict[str, Any],
    stage1: Dict[str, Any],
    stage2: Dict[str, Any],
) -> Optional[str]:
    """Save a markdown report combining Stage 1 + Stage 2 results."""

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    company = _normalize(job.get("company"), "unknown")
    title = _normalize(job.get("title"), "unknown")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = company.lower().replace(" ", "-").replace("/", "-")[:30]

    # Find next report number
    existing = list(REPORTS_DIR.glob("*.md"))
    max_num = 0
    for f in existing:
        try:
            num = int(f.name.split("-")[0])
            max_num = max(max_num, num)
        except (ValueError, IndexError):
            pass
    report_num = max_num + 1
    filename = f"{report_num:03d}-{slug}-{date_str}.md"
    filepath = REPORTS_DIR / filename

    # Build report
    lines = [
        f"# {company} — {title}",
        "",
        f"**Date:** {date_str}",
        f"**Archetype:** {stage1.get('archetype', 'N/A')}",
        f"**Screen Score:** {stage1.get('screen_score', 'N/A')}/5",
        f"**Deep Eval Score:** {stage2.get('deep_eval_score', 'N/A')}/5",
        f"**Final Route:** {stage2.get('final_route', 'N/A')}",
        f"**Legitimacy:** {stage2.get('legitimacy_tier', 'N/A')}",
        f"**URL:** {_normalize(job.get('url'), 'N/A')}",
        "",
        "---",
        "",
        "## A) Role Summary",
        "",
        f"- **Archetype:** {stage1.get('archetype', 'N/A')} (confidence: {stage1.get('archetype_confidence', 'N/A')})",
        f"- **Secondary:** {stage1.get('archetype_secondary') or 'None'}",
        f"- **Domain:** {stage1.get('domain', 'N/A')}",
        f"- **Function:** {stage1.get('function', 'N/A')}",
        f"- **Seniority:** {stage1.get('seniority_detected', 'N/A')}",
        f"- **Location:** {_normalize(job.get('location'))}",
        f"- **Compensation:** {_normalize(job.get('compensation'))}",
        f"- **Company Score:** {company_result.get('company_interest_score', 'N/A')}/10",
        "",
        "---",
        "",
        "## B) Resume Match",
        "",
        f"**Score:** {stage1.get('resume_match_score', 'N/A')}/5",
        f"**Gap Severity:** {stage1.get('gap_severity', 'N/A')}",
        "",
        "**Top Strengths:**",
    ]
    for s in stage1.get("resume_match_top_strengths", []):
        lines.append(f"- {s}")

    lines.extend(["", "**Gaps:**"])
    for g in stage1.get("resume_gaps", []):
        lines.append(f"- {g}")

    lines.extend([
        "",
        "---",
        "",
        "## C) Level Fit",
        "",
        f"**Score:** {stage1.get('level_fit_score', 'N/A')}/5",
        f"**Assessment:** {stage1.get('level_assessment', 'N/A')}",
        "",
        f"**Why Strong:** {stage1.get('differentiation_reason', 'N/A')}",
        f"**Main Reservation:** {stage1.get('main_reservation', 'N/A')}",
        "",
        "---",
        "",
        "## D) Compensation & Market Demand",
        "",
        f"**Comp Assessment:** {stage2.get('comp_assessment', 'N/A')}",
    ])

    low = stage2.get("comp_estimate_low")
    high = stage2.get("comp_estimate_high")
    if low or high:
        low_str = f"${low:,.0f}" if low else "?"
        high_str = f"${high:,.0f}" if high else "?"
        lines.append(f"**Estimated Range:** {low_str} – {high_str}")
    lines.extend([
        f"**Notes:** {stage2.get('comp_notes', 'N/A')}",
        f"**Role Demand:** {stage2.get('role_demand', 'N/A')}",
        f"**Hiring Context:** {stage2.get('hiring_context', 'N/A')}",
        "",
        "---",
        "",
        "## E) Resume Personalization",
        "",
        f"**Positioning:** {stage2.get('positioning_summary', 'N/A')}",
        "",
        "**Recommended Changes:**",
        "",
    ])
    for i, change in enumerate(stage2.get("resume_changes", []), 1):
        lines.extend([
            f"{i}. **{change.get('section', '')}**",
            f"   - Current: {change.get('current', '')}",
            f"   - Proposed: {change.get('proposed', '')}",
            f"   - Why: {change.get('why', '')}",
            "",
        ])

    lines.extend([
        "---",
        "",
        "## F) Interview Preparation",
        "",
    ])
    for i, story in enumerate(stage2.get("interview_stories", []), 1):
        skel = story.get("star_skeleton", {})
        lines.extend([
            f"### Story {i}: {story.get('requirement', '')}",
            f"**Experience:** {story.get('experience_match', '')}",
            f"- **S:** {skel.get('situation', '')}",
            f"- **T:** {skel.get('task', '')}",
            f"- **A:** {skel.get('action', '')}",
            f"- **R:** {skel.get('result', '')}",
            f"- **Reflection:** {skel.get('reflection', '')}",
            "",
        ])

    lines.append("**Red Flag Questions:**")
    for q in stage2.get("red_flag_questions", []):
        lines.append(f"- **Q:** {q.get('question', '')}")
        lines.append(f"  **A:** {q.get('approach', '')}")

    case = stage2.get("case_study_recommendation", {})
    lines.extend([
        "",
        f"**Case Study:** {case.get('project', 'N/A')} — {case.get('why', '')}",
        "",
        "---",
        "",
        "## G) Posting Legitimacy",
        "",
        f"**Tier:** {stage2.get('legitimacy_tier', 'N/A')}",
        "",
    ])
    for sig in stage2.get("legitimacy_signals", []):
        lines.append(f"- [{sig.get('weight', '')}] {sig.get('signal', '')}: {sig.get('finding', '')}")

    notes = stage2.get("legitimacy_notes", "")
    if notes:
        lines.extend(["", f"**Notes:** {notes}"])

    lines.extend([
        "",
        "---",
        "",
        f"## Summary",
        "",
        f"**Deep Eval Score:** {stage2.get('deep_eval_score', 'N/A')}/5",
        f"**Final Route:** {stage2.get('final_route', 'N/A')}",
        f"**Urgency:** {stage2.get('apply_urgency', 'N/A')}",
        f"**One-liner:** {stage2.get('one_line_summary', 'N/A')}",
    ])

    report_text = "\n".join(lines)
    filepath.write_text(report_text, encoding="utf-8")
    print(f"  [report] saved {filepath.name}")

    return str(filepath)
