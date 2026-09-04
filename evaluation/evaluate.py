"""
evaluation/evaluate.py
----------------------
Grounded LLM evaluation system for ReelForge.

Audits structured Brain Objects against available source evidence
(caption, transcript, vision analysis, creator, URL) using an LLM judge
routed through OmniRoute (processing.llm_client.generate_json).

Scoring Rubric (7 Dimensions, Normalized to 0–100):
  - Category Accuracy (0–10)
  - Grounding / Factuality (0–20)
  - Completeness (0–15)
  - Summary / Insight Quality (0–15)
  - Structured Extraction Accuracy (0–20)
  - Tags / Tools / Resources (0–10)
  - Multimodal Utilization (0–10 when applicable, or not_applicable)

Quality Bands:
  - 90–100: excellent
  - 75–89 : good
  - 60–74 : fair
  - 40–59 : poor
  - 0–39  : critical
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from config import EVALUATION_MODEL, TEXT_MODEL
from processing.llm_client import generate_json

EVALUATION_VERSION = "2.7.0"

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "evaluation_judge.txt"
if not _PROMPT_PATH.exists():
    # Fallback to local evaluation folder if prompts/ is not adjacent
    _PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.txt"


class EvaluationValidationError(ValueError):
    """Raised when the LLM judge output fails structural or schema validation."""
    pass


DIMENSION_MAX_SCORES: dict[str, int] = {
    "category_accuracy": 10,
    "grounding_factuality": 20,
    "completeness": 15,
    "summary_quality": 15,
    "structured_extraction": 20,
    "tags_tools_resources": 10,
    "multimodal_utilization": 10,
}


def _load_prompt_template() -> str:
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(f"Evaluation prompt template not found at '{_PROMPT_PATH}'.")
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _format_evidence_text(val: Any) -> str:
    if val is None:
        return "[None provided]"
    if isinstance(val, str):
        cleaned = val.strip()
        return cleaned if cleaned else "[None provided]"
    if isinstance(val, (dict, list)):
        return json.dumps(val, indent=2, ensure_ascii=False)
    return str(val)


def build_evaluation_prompt(
    reel_id: str,
    source_url: str | None,
    creator: str | None,
    caption: str | None,
    transcript: str | None,
    vision_analysis: Any,
    category: str,
    knowledge: dict,
) -> str:
    """Build the prompt for the grounded evaluation judge."""
    template = _load_prompt_template()

    knowledge_clean = {k: v for k, v in knowledge.items() if v is not None}
    knowledge_str = json.dumps(knowledge_clean, indent=2, ensure_ascii=False)

    prompt = (
        template
        .replace("{{reel_id}}", str(reel_id or "Unknown"))
        .replace("{{source_url}}", str(source_url or "Unknown"))
        .replace("{{creator}}", str(creator or "Unknown"))
        .replace("{{caption}}", _format_evidence_text(caption))
        .replace("{{transcript}}", _format_evidence_text(transcript))
        .replace("{{vision_analysis}}", _format_evidence_text(vision_analysis))
        .replace("{{category}}", str(category or "Unknown"))
        .replace("{{knowledge_json}}", knowledge_str)
    )
    return prompt


def _clamp(val: Any, min_val: int, max_val: int, default: int = 0) -> int:
    try:
        num = int(val)
        return max(min_val, min(num, max_val))
    except (ValueError, TypeError):
        return default


def compute_normalized_score(dimensions: dict) -> tuple[float, str]:
    """
    Deterministically compute the overall score (0–100) and quality band
    by normalizing across applicable dimensions.
    """
    total_obtained = 0.0
    max_possible = 0.0

    # 1. Base 6 Dimensions (Total = 90)
    for dim_key in (
        "category_accuracy",
        "grounding_factuality",
        "completeness",
        "summary_quality",
        "structured_extraction",
        "tags_tools_resources",
    ):
        dim_data = dimensions.get(dim_key) or {}
        max_score = DIMENSION_MAX_SCORES[dim_key]
        score = _clamp(dim_data.get("score"), 0, max_score, default=0)
        total_obtained += score
        max_possible += max_score

    # 2. Multimodal Dimension (Optional +10)
    mm_data = dimensions.get("multimodal_utilization") or {}
    mm_applicable = bool(mm_data.get("applicable", False))
    if mm_applicable and mm_data.get("score") is not None:
        mm_score = _clamp(mm_data.get("score"), 0, 10, default=0)
        total_obtained += mm_score
        max_possible += 10.0

    if max_possible <= 0:
        overall_score = 0.0
    else:
        overall_score = round((total_obtained / max_possible) * 100, 1)

    # Quality Band Mapping
    if overall_score >= 90.0:
        quality_band = "excellent"
    elif overall_score >= 75.0:
        quality_band = "good"
    elif overall_score >= 60.0:
        quality_band = "fair"
    elif overall_score >= 40.0:
        quality_band = "poor"
    else:
        quality_band = "critical"

    return overall_score, quality_band


def validate_and_normalize_judge_output(
    raw_dict: dict,
    reel_id: str,
    category: str,
    available_modalities: dict,
    judge_model: str,
) -> dict:
    """Validate and clean judge output, ensuring consistent types and deterministic scoring."""
    if not isinstance(raw_dict, dict):
        raise EvaluationValidationError(f"Judge output must be a dictionary, got {type(raw_dict).__name__}")

    raw_dimensions = raw_dict.get("dimensions")
    if not isinstance(raw_dimensions, dict):
        raise EvaluationValidationError("Judge output missing 'dimensions' object.")

    validated_dimensions: dict[str, Any] = {}

    # Category Accuracy
    cat_data = raw_dimensions.get("category_accuracy") or {}
    verdict = str(cat_data.get("verdict", "questionable")).lower()
    if verdict not in ("correct", "questionable", "incorrect"):
        verdict = "questionable"
    validated_dimensions["category_accuracy"] = {
        "score": _clamp(cat_data.get("score"), 0, 10, default=5),
        "verdict": verdict,
        "reasoning": str(cat_data.get("reasoning", "")).strip(),
    }

    # Standard Dimensions
    for dim_key in (
        "grounding_factuality",
        "completeness",
        "summary_quality",
        "structured_extraction",
        "tags_tools_resources",
    ):
        d_data = raw_dimensions.get(dim_key) or {}
        max_score = DIMENSION_MAX_SCORES[dim_key]
        validated_dimensions[dim_key] = {
            "score": _clamp(d_data.get("score"), 0, max_score, default=0),
            "reasoning": str(d_data.get("reasoning", "")).strip(),
        }

    # Multimodal Dimension
    mm_data = raw_dimensions.get("multimodal_utilization") or {}
    has_mm_evidence = bool(available_modalities.get("transcript") or available_modalities.get("vision_analysis"))
    mm_applicable = bool(mm_data.get("applicable", has_mm_evidence))

    if not has_mm_evidence:
        mm_applicable = False
        mm_score = None
    elif mm_data.get("score") is not None:
        mm_score = _clamp(mm_data.get("score"), 0, 10, default=5)
    else:
        mm_score = 5

    validated_dimensions["multimodal_utilization"] = {
        "applicable": mm_applicable,
        "score": mm_score,
        "reasoning": str(mm_data.get("reasoning", "")).strip(),
    }

    # Issues Validation
    raw_issues = raw_dict.get("issues") or []
    validated_issues = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, dict):
                sev = str(item.get("severity", "minor")).lower()
                if sev not in ("critical", "major", "minor"):
                    sev = "minor"
                validated_issues.append({
                    "severity": sev,
                    "field": str(item.get("field", "knowledge")).strip(),
                    "problem": str(item.get("problem", "Issue identified")).strip(),
                    "evidence": str(item.get("evidence", "")).strip(),
                    "recommendation": str(item.get("recommendation", "")).strip(),
                })

    # Strengths Validation
    raw_strengths = raw_dict.get("strengths") or []
    validated_strengths = []
    if isinstance(raw_strengths, list):
        for s in raw_strengths:
            if isinstance(s, str) and s.strip():
                validated_strengths.append(s.strip())

    overall_score, quality_band = compute_normalized_score(validated_dimensions)

    return {
        "evaluation_version": EVALUATION_VERSION,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "reel_id": reel_id,
        "category": category,
        "available_modalities": available_modalities,
        "judge_model": judge_model,
        "judge_type": "llm_grounded",
        "dimensions": validated_dimensions,
        "overall_score": overall_score,
        "quality_band": quality_band,
        "issues": validated_issues,
        "strengths": validated_strengths,
    }


def evaluate_reel_evidence(
    reel_id: str,
    source_url: str | None,
    creator: str | None,
    caption: str | None,
    transcript: str | None,
    vision_analysis: Any,
    category: str,
    knowledge: dict,
    model: str | None = None,
) -> dict:
    """
    Evaluate a single Reel using the grounded OmniRoute LLM judge.
    Returns the validated evaluation dictionary.
    """
    target_model = model or EVALUATION_MODEL or TEXT_MODEL

    available_modalities = {
        "caption": bool(caption and isinstance(caption, str) and caption.strip()),
        "transcript": bool(transcript and isinstance(transcript, str) and transcript.strip()),
        "vision_analysis": bool(vision_analysis),
    }

    prompt = build_evaluation_prompt(
        reel_id=reel_id,
        source_url=source_url,
        creator=creator,
        caption=caption,
        transcript=transcript,
        vision_analysis=vision_analysis,
        category=category,
        knowledge=knowledge,
    )

    raw_response = generate_json(
        prompt=prompt,
        model=target_model,
        temperature=0.1,
    )

    evaluation = validate_and_normalize_judge_output(
        raw_dict=raw_response,
        reel_id=reel_id,
        category=category,
        available_modalities=available_modalities,
        judge_model=target_model,
    )
    return evaluation


def evaluate_brain_object(brain: dict, model: str | None = None) -> dict:
    """
    Evaluate a loaded Brain Object dictionary against its internal source evidence.
    Does NOT modify the Brain Object.
    """
    reel_id = brain.get("id") or "Unknown"
    source = brain.get("source") or {}
    creator_obj = brain.get("creator") or {}
    content = brain.get("content") or {}
    knowledge = brain.get("knowledge") or {}

    source_url = source.get("url")
    username = creator_obj.get("username")
    full_name = creator_obj.get("full_name")
    creator_str = f"@{username} ({full_name})" if username and full_name else (f"@{username}" if username else None)

    caption = content.get("caption")
    transcript = content.get("transcript")
    vision_analysis = content.get("vision_analysis")
    category = knowledge.get("category") or "Other"

    return evaluate_reel_evidence(
        reel_id=reel_id,
        source_url=source_url,
        creator=creator_str,
        caption=caption,
        transcript=transcript,
        vision_analysis=vision_analysis,
        category=category,
        knowledge=knowledge,
        model=model,
    )


# ---------------------------------------------------------------------------
# Legacy compatibility alias
# ---------------------------------------------------------------------------
def evaluate_reel(
    reel_id: str,
    expected_category: str | None = None,
    caption: str | None = None,
    transcript: str | None = None,
    brain_object: dict | None = None,
    vision_analysis: Any = None,
    model: str | None = None,
) -> dict:
    """Legacy compatibility helper."""
    if brain_object:
        b = dict(brain_object)
        if caption is not None:
            b.setdefault("content", {})["caption"] = caption
        if transcript is not None:
            b.setdefault("content", {})["transcript"] = transcript
        if vision_analysis is not None:
            b.setdefault("content", {})["vision_analysis"] = vision_analysis
        return evaluate_brain_object(b, model=model)

    knowledge = {"category": expected_category or "Other"}
    return evaluate_reel_evidence(
        reel_id=reel_id,
        source_url=None,
        creator=None,
        caption=caption,
        transcript=transcript,
        vision_analysis=vision_analysis,
        category=expected_category or "Other",
        knowledge=knowledge,
        model=model,
    )
