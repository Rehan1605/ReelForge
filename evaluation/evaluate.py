"""
evaluation/evaluate.py
----------------------
Evidence-first single-LLM evaluation pipeline for InstaBrain.

Architecture
------------
    Source evidence + Brain Object
            ↓
    Evidence-first LLM judge (finding-oriented, no scoring)
            ↓
    Field-level findings (CORRECT / MISSING / INCORRECT /
                          HALLUCINATED / IRRELEVANT / NOT_APPLICABLE)
            ↓
    Python: _score_from_findings() → dimension scores
            ↓
    Python: _compute_overall_score() → deterministic weighted total
            ↓
    Final evaluation dict

The LLM is NOT asked to produce numerical scores.
All scoring is deterministic Python.

Public API
----------
    evaluate_reel(reel_id, expected_category, caption, transcript, brain_object)
        -> dict  (validated evaluation result)

CLI
---
    python evaluation/evaluate.py path/to/test_case.json [--save output.json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.txt"
_OLLAMA_URL  = "http://localhost:11434/api/generate"

try:
    from config import TEXT_MODEL as _JUDGE_MODEL
except Exception:
    _JUDGE_MODEL = "qwen2.5:7b-instruct"

# ---------------------------------------------------------------------------
# Schema constants — new evidence-first contract
# ---------------------------------------------------------------------------

# Fields the LLM must return at the top level.
_REQUIRED_FIELDS = [
    "reel_id",
    "category_evaluation",
    "findings",
    "critical_error",
    "strengths",
    "final_verdict",
]

# Keys required inside category_evaluation.
_REQUIRED_CATEGORY_EVAL_KEYS = ["expected", "actual", "correct", "evidence"]

# Keys required inside each finding object.
_REQUIRED_FINDING_KEYS = ["field", "status", "claim", "evidence"]

# The only valid status values.
_VALID_STATUSES = {
    "CORRECT",
    "MISSING",
    "INCORRECT",
    "HALLUCINATED",
    "IRRELEVANT",
    "NOT_APPLICABLE",
}

# ---------------------------------------------------------------------------
# Rubric weights (§14) — used only by Python, never sent to the LLM.
# website_accuracy and tag_quality are derived and reported separately;
# they are intentionally absent from the weighted formula.
# ---------------------------------------------------------------------------
_SCORE_WEIGHTS: dict[str, int] = {
    "category_accuracy":  15,
    "factual_accuracy":   25,
    "hallucination":      20,
    "completeness":       15,
    "relevance":          10,
    "summary_quality":    10,
    "structured_quality":  5,
}
assert sum(_SCORE_WEIGHTS.values()) == 100, "Score weights must sum to 100"

# ---------------------------------------------------------------------------
# Deterministic scoring from findings
# ---------------------------------------------------------------------------

def _score_from_findings(result: dict) -> dict:
    """
    Derive all dimension scores from the LLM's field-level findings.

    This replaces the old approach of trusting the LLM to assign numbers.
    The LLM's job was finding; our job is scoring.

    Scoring rules
    -------------

    category_accuracy
        Comes directly from category_evaluation.correct:
          True  → 5
          False → 0
        (The rubric's intermediate values 1–4 require human judgment on
        ambiguous categories. For binary correct/incorrect from the LLM
        we use the endpoints. A future multi-judge pass can refine this.)

    hallucination  (inverted: 5 = clean, 0 = all fabricated)
        Count HALLUCINATED findings.
          0              → 5
          1              → 4
          2              → 3
          3–4            → 2
          5+             → 1
          All/most fields HALLUCINATED (>= half of total findings) → 0

    factual_accuracy
        Based on INCORRECT + HALLUCINATED findings (both represent
        content that does not match the source).
          0 bad findings → 5
          1              → 4
          2              → 3
          3–4            → 2
          5+             → 1
          >= half bad    → 0

    completeness
        Based on MISSING findings.
          0 MISSING → 5
          1         → 4
          2         → 3
          3–4       → 2
          5+        → 1
          >= half of source fields MISSING → 0

    relevance
        Based on IRRELEVANT findings.
          0 IRRELEVANT → 5
          1            → 4
          2–3          → 3
          4+           → 2
          (Relevance rarely hits 0–1 from findings alone; floor at 2
          unless most findings are IRRELEVANT.)

    summary_quality
        Locate findings where field == "summary":
          No summary finding at all → cannot determine → 3 (neutral)
          Summary finding is CORRECT → 5
          Summary finding is MISSING → 1
          Summary finding is INCORRECT or HALLUCINATED → 0
          Summary finding is IRRELEVANT → 2

    structured_quality
        Look at CORRECT vs (INCORRECT + HALLUCINATED + MISSING) across
        all non-summary, non-title, non-tag, non-website, non-category fields.
          All correct        → 5
          Mostly correct     → 4
          Half-half          → 3
          Mostly problems    → 2
          All problems       → 1
          No structured data → 3 (neutral)

    website_accuracy  (reported separately, not in weighted formula)
        Locate findings where field == "websites":
          No website finding → 5 (nothing to evaluate, correct by default)
          CORRECT            → 5
          MISSING            → 3
          HALLUCINATED       → 0
          INCORRECT          → 1

    tag_quality  (reported separately, not in weighted formula)
        Locate findings where field == "tags":
          No tag finding     → 3 (neutral)
          CORRECT            → 5
          MISSING            → 3
          HALLUCINATED       → 0
          IRRELEVANT         → 2

    All scores are clamped to 0–5.
    """
    findings = result.get("findings", [])
    cat_correct = result.get("category_evaluation", {}).get("correct", False)

    # Partition findings by status
    hallucinated = [f for f in findings if f.get("status") == "HALLUCINATED"]
    incorrect    = [f for f in findings if f.get("status") == "INCORRECT"]
    missing      = [f for f in findings if f.get("status") == "MISSING"]
    irrelevant   = [f for f in findings if f.get("status") == "IRRELEVANT"]
    total        = len(findings)

    # ── category_accuracy ────────────────────────────────────────────────
    category_accuracy = 5 if cat_correct else 0

    # ── hallucination ─────────────────────────────────────────────────────
    n_h = len(hallucinated)
    if n_h == 0:
        hallucination = 5
    elif n_h == 1:
        hallucination = 4
    elif n_h == 2:
        hallucination = 3
    elif n_h <= 4:
        hallucination = 2
    elif total > 0 and n_h >= total / 2:
        hallucination = 0
    else:
        hallucination = 1

    # ── factual_accuracy ──────────────────────────────────────────────────
    n_bad = len(incorrect) + len(hallucinated)
    if n_bad == 0:
        factual_accuracy = 5
    elif n_bad == 1:
        factual_accuracy = 4
    elif n_bad == 2:
        factual_accuracy = 3
    elif n_bad <= 4:
        factual_accuracy = 2
    elif total > 0 and n_bad >= total / 2:
        factual_accuracy = 0
    else:
        factual_accuracy = 1

    # ── completeness ──────────────────────────────────────────────────────
    n_m = len(missing)
    if n_m == 0:
        completeness = 5
    elif n_m == 1:
        completeness = 4
    elif n_m == 2:
        completeness = 3
    elif n_m <= 4:
        completeness = 2
    elif total > 0 and n_m >= total / 2:
        completeness = 0
    else:
        completeness = 1

    # ── relevance ─────────────────────────────────────────────────────────
    n_ir = len(irrelevant)
    if n_ir == 0:
        relevance = 5
    elif n_ir == 1:
        relevance = 4
    elif n_ir <= 3:
        relevance = 3
    else:
        relevance = 2   # floor at 2; 0–1 requires human judgment

    # ── summary_quality ───────────────────────────────────────────────────
    summary_findings = [f for f in findings
                        if f.get("field", "").lower() in ("summary", "title")]
    if not summary_findings:
        summary_quality = 3   # neutral — no evidence either way
    else:
        # Use the worst summary/title finding
        statuses = {f.get("status") for f in summary_findings}
        if "HALLUCINATED" in statuses or "INCORRECT" in statuses:
            summary_quality = 0
        elif "MISSING" in statuses:
            summary_quality = 1
        elif "IRRELEVANT" in statuses:
            summary_quality = 2
        else:
            summary_quality = 5   # all CORRECT or NOT_APPLICABLE

    # ── structured_quality ────────────────────────────────────────────────
    _non_structural = {"summary", "title", "tags", "websites", "category",
                       "reel_id", "id", "status"}
    structured_findings = [
        f for f in findings
        if f.get("field", "").lower() not in _non_structural
    ]
    if not structured_findings:
        structured_quality = 3   # neutral
    else:
        n_ok  = sum(1 for f in structured_findings
                    if f.get("status") in ("CORRECT", "NOT_APPLICABLE"))
        n_tot = len(structured_findings)
        ratio = n_ok / n_tot
        if ratio == 1.0:
            structured_quality = 5
        elif ratio >= 0.8:
            structured_quality = 4
        elif ratio >= 0.5:
            structured_quality = 3
        elif ratio >= 0.25:
            structured_quality = 2
        else:
            structured_quality = 1

    # ── website_accuracy (reported separately) ────────────────────────────
    website_findings = [f for f in findings
                        if f.get("field", "").lower() in ("websites", "website", "urls")]
    if not website_findings:
        website_accuracy = 5   # no URLs in source or output — correct by default
    else:
        worst = {f.get("status") for f in website_findings}
        if "HALLUCINATED" in worst:
            website_accuracy = 0
        elif "INCORRECT" in worst:
            website_accuracy = 1
        elif "MISSING" in worst:
            website_accuracy = 3
        else:
            website_accuracy = 5

    # ── tag_quality (reported separately) ─────────────────────────────────
    tag_findings = [f for f in findings
                    if f.get("field", "").lower() in ("tags", "tag")]
    if not tag_findings:
        tag_quality = 3   # neutral
    else:
        worst = {f.get("status") for f in tag_findings}
        if "HALLUCINATED" in worst:
            tag_quality = 0
        elif "IRRELEVANT" in worst:
            tag_quality = 2
        elif "MISSING" in worst:
            tag_quality = 3
        else:
            tag_quality = 5

    return {
        "category_accuracy":  category_accuracy,
        "factual_accuracy":   factual_accuracy,
        "hallucination":      hallucination,
        "completeness":       completeness,
        "relevance":          relevance,
        "summary_quality":    summary_quality,
        "structured_quality": structured_quality,
        "website_accuracy":   website_accuracy,
        "tag_quality":        tag_quality,
    }


def _compute_overall_score(scores: dict) -> int:
    """
    Calculate the authoritative overall score from rubric weights.

    Formula (rubric §14):
        sum(score / 5 * weight)  for each weighted dimension
    Rounded to the nearest integer, clamped to [0, 100].

    website_accuracy and tag_quality are excluded — the rubric formula
    does not assign them a weight.
    """
    weighted_sum = 0
    for key, weight in _SCORE_WEIGHTS.items():
        weighted_sum += scores.get(key, 0) * weight
    overall = round(weighted_sum / 5)
    return max(0, min(100, overall))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences the model may wrap around JSON."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def _load_prompt() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Judge prompt not found: {_PROMPT_PATH}\n"
            "Expected: evaluation/judge_prompt.txt"
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_prompt(
    reel_id: str,
    expected_category: str,
    caption: str,
    transcript: str,
    brain_object: dict,
) -> str:
    """
    Fill the five known placeholders using plain str.replace().

    str.format() is intentionally avoided — the prompt contains literal
    JSON braces that would trigger a KeyError with str.format().
    """
    template = _load_prompt()
    replacements = {
        "{reel_id}":            reel_id,
        "{expected_category}":  expected_category,
        "{caption}":            caption or "(no caption provided)",
        "{transcript}":         transcript or "(no transcript provided)",
        "{brain_object}":       json.dumps(brain_object, indent=2, ensure_ascii=False),
    }
    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result


def _call_ollama(prompt: str) -> str:
    """POST prompt to local Ollama. Returns raw response string."""
    try:
        response = requests.post(
            _OLLAMA_URL,
            json={
                "model":  _JUDGE_MODEL,
                "prompt": prompt,
                "stream": False,
                # Native Ollama JSON mode — constrains token sampling so the
                # model can only emit valid JSON tokens.
                "format": "json",
            },
            timeout=300,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {_OLLAMA_URL}. "
            "Is Ollama running?  Try: ollama serve"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            "Ollama request timed out after 300 seconds. "
            "The model may be too slow for this prompt size."
        )
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"Ollama HTTP error: {exc}") from exc

    payload = response.json()
    raw = payload.get("response", "")
    if not raw:
        raise RuntimeError(
            f"Ollama returned an empty response. Full payload: {payload}"
        )
    return raw


def _parse_judge_output(raw: str) -> dict:
    """
    Parse the judge's raw output into a Python dict.
    Handles plain JSON and ```json-fenced JSON.
    Raises ValueError with a useful message on malformed JSON.
    """
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        snippet = cleaned[:300] + ("..." if len(cleaned) > 300 else "")
        raise ValueError(
            f"Judge returned malformed JSON.\n"
            f"JSON error: {exc}\n"
            f"Raw output (first 300 chars):\n{snippet}"
        ) from exc


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class EvaluationValidationError(Exception):
    """Raised when the judge output does not conform to the evidence-first schema."""


def _validate(result: dict) -> list[str]:
    """
    Validate the judge's finding-oriented output.
    Returns a list of error strings; empty list = valid.
    Strings prefixed 'WARNING:' are soft consistency warnings, not hard errors.
    """
    errors: list[str] = []

    # Top-level required fields
    for field in _REQUIRED_FIELDS:
        if field not in result:
            errors.append(f"Missing required top-level field: '{field}'")

    if errors:
        return errors   # can't safely go deeper

    # category_evaluation
    cat_eval = result.get("category_evaluation", {})
    if not isinstance(cat_eval, dict):
        errors.append("'category_evaluation' must be an object.")
    else:
        for key in _REQUIRED_CATEGORY_EVAL_KEYS:
            if key not in cat_eval:
                errors.append(f"'category_evaluation' missing key: '{key}'")
        if not isinstance(cat_eval.get("correct"), bool):
            errors.append(
                f"'category_evaluation.correct' must be a boolean, "
                f"got: {cat_eval.get('correct')!r}"
            )
        if not isinstance(cat_eval.get("evidence"), str) or not cat_eval.get("evidence", "").strip():
            errors.append("'category_evaluation.evidence' must be a non-empty string.")

    # findings
    findings = result.get("findings")
    if not isinstance(findings, list):
        errors.append("'findings' must be an array.")
    else:
        for i, finding in enumerate(findings):
            if not isinstance(finding, dict):
                errors.append(f"'findings[{i}]' must be an object.")
                continue
            for key in _REQUIRED_FINDING_KEYS:
                if key not in finding:
                    errors.append(f"'findings[{i}]' missing key: '{key}'")
            status = finding.get("status")
            if status not in _VALID_STATUSES:
                errors.append(
                    f"'findings[{i}].status' must be one of "
                    f"{sorted(_VALID_STATUSES)}, got: {status!r}"
                )
            # evidence must be non-empty for problematic findings
            if status in ("HALLUCINATED", "INCORRECT", "MISSING"):
                ev = finding.get("evidence", "")
                if not isinstance(ev, str) or not ev.strip():
                    errors.append(
                        f"'findings[{i}]' has status={status!r} "
                        "but 'evidence' is empty. Evidence is required for this status."
                    )

    # critical_error
    if not isinstance(result.get("critical_error"), bool):
        errors.append(
            f"'critical_error' must be a boolean, "
            f"got: {result.get('critical_error')!r}"
        )

    # strengths
    if not isinstance(result.get("strengths"), list):
        errors.append("'strengths' must be an array.")

    # final_verdict
    if not isinstance(result.get("final_verdict"), str) or not result["final_verdict"].strip():
        errors.append("'final_verdict' must be a non-empty string.")

    # Consistency warnings — only run when findings are structurally valid dicts
    if isinstance(findings, list) and all(isinstance(f, dict) for f in findings):
        hallucinated_findings = [f for f in findings if f.get("status") == "HALLUCINATED"]
        missing_findings      = [f for f in findings if f.get("status") == "MISSING"]
        ce = result.get("critical_error", False)

        # Fabricated URL findings should always trigger critical_error
        fabricated_urls = [
            f for f in hallucinated_findings
            if "url" in f.get("field", "").lower() or "website" in f.get("field", "").lower()
        ]
        if fabricated_urls and not ce:
            errors.append(
                "WARNING: hallucinated website/URL findings are present but "
                "critical_error is false. Fabricated URLs should trigger critical_error=true."
            )

        # Detect problems buried in final_verdict but absent from findings[].
        # We look for hallucination/missing language in the verdict and compare
        # against the structured findings count.  This is a heuristic — we do
        # NOT parse or extract findings from prose; we only warn.
        verdict = result.get("final_verdict", "").lower()
        _halluc_keywords = ("hallucinated", "hallucination", "fabricated",
                            "invented", "not in the source", "not mentioned")
        _missing_keywords = ("missing", "omitted", "not captured", "absent from")

        verdict_mentions_hallucination = any(kw in verdict for kw in _halluc_keywords)
        verdict_mentions_missing       = any(kw in verdict for kw in _missing_keywords)

        if verdict_mentions_hallucination and len(hallucinated_findings) == 0:
            errors.append(
                "WARNING: final_verdict mentions hallucination/fabrication but "
                "findings[] contains zero HALLUCINATED entries. "
                "Problems mentioned in final_verdict must also appear as structured "
                "findings. The scoring system cannot see prose-only issues."
            )

        if verdict_mentions_missing and len(missing_findings) == 0:
            errors.append(
                "WARNING: final_verdict mentions missing/omitted information but "
                "findings[] contains zero MISSING entries. "
                "Problems mentioned in final_verdict must also appear as structured "
                "findings. The scoring system cannot see prose-only issues."
            )

    return errors


# ---------------------------------------------------------------------------
# Quality classification (rubric §15)
# ---------------------------------------------------------------------------

def _classify(overall_score: int | float, critical_error: bool) -> str:
    """Map overall_score + critical_error to a quality classification."""
    if overall_score >= 90:
        return "Good" if critical_error else "Excellent"
    if overall_score >= 80:
        return "Good"
    if overall_score >= 70:
        return "Acceptable"
    if overall_score >= 60:
        return "Needs Improvement"
    return "Poor"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_reel(
    reel_id: str,
    expected_category: str,
    caption: str,
    transcript: str,
    brain_object: dict,
) -> dict:
    """
    Evaluate a single reel using the local Ollama evidence-first judge.

    Parameters
    ----------
    reel_id            : str   Identifier for the reel.
    expected_category  : str   The correct category (human-verified).
    caption            : str   Original Instagram caption.
    transcript         : str   Whisper transcript of the reel audio.
    brain_object       : dict  The full Brain Object generated by InstaBrain.

    Returns
    -------
    dict  Validated evaluation result with deterministic scores.

    Raises
    ------
    FileNotFoundError         if judge_prompt.txt is missing.
    RuntimeError              if Ollama is unavailable or returns empty response.
    ValueError                if the judge returns malformed JSON.
    EvaluationValidationError if the judge JSON fails schema validation.
    """
    print(f"Evaluating {reel_id}...")
    print(f"Judge model: {_JUDGE_MODEL}")

    prompt = _build_prompt(
        reel_id, expected_category, caption, transcript, brain_object
    )

    raw    = _call_ollama(prompt)
    result = _parse_judge_output(raw)

    # Pin reel_id — the judge may echo it or leave it blank.
    result["reel_id"] = reel_id

    validation_errors = _validate(result)
    hard_errors = [e for e in validation_errors if not e.startswith("WARNING")]
    warnings    = [e for e in validation_errors if e.startswith("WARNING")]

    if hard_errors:
        formatted = "\n  ".join(hard_errors)
        raise EvaluationValidationError(
            f"Judge output failed schema validation "
            f"({len(hard_errors)} error(s)):\n  {formatted}\n\n"
            f"Raw judge output (first 500 chars):\n{raw[:500]}"
        )

    for w in warnings:
        print(f"  [warn] {w}")

    # ------------------------------------------------------------------
    # Deterministic scoring — derived entirely from findings, not from
    # any numbers the LLM may have emitted.
    # ------------------------------------------------------------------
    scores  = _score_from_findings(result)
    overall = _compute_overall_score(scores)

    result["scores"]        = scores
    result["overall_score"] = overall

    result["quality_classification"] = _classify(overall, result["critical_error"])

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_summary(result: dict) -> None:
    """Print a concise human-readable summary."""
    cat    = result.get("category_evaluation", {})
    scores = result.get("scores", {})
    findings = result.get("findings", [])

    print()
    print(f"  Reel ID        : {result.get('reel_id', '?')}")
    print(f"  Category       : {cat.get('actual', '?')} "
          f"(expected: {cat.get('expected', '?')}) — "
          f"{'✓' if cat.get('correct') else '✗'}")
    print(f"  Cat. evidence  : {cat.get('evidence', '')}")
    print()

    # Finding summary
    from collections import Counter
    status_counts = Counter(f.get("status") for f in findings)
    print(f"  Findings ({len(findings)} total):")
    for status in ("CORRECT", "MISSING", "INCORRECT", "HALLUCINATED",
                   "IRRELEVANT", "NOT_APPLICABLE"):
        n = status_counts.get(status, 0)
        if n:
            print(f"    {status:<18} {n}")

    print()
    print("  Derived dimension scores (0–5):")
    for key in ("factual_accuracy", "hallucination", "completeness", "relevance",
                "summary_quality", "structured_quality", "website_accuracy", "tag_quality"):
        print(f"    {key:<22} {scores.get(key, '?')}")

    print()
    print(f"  Overall score  : {result.get('overall_score', '?')}/100  (deterministic)")
    print(f"  Classification : {result.get('quality_classification', '?')}")
    print(f"  Critical error : {result.get('critical_error', '?')}")

    strengths = result.get("strengths", [])
    if strengths:
        print(f"  Strengths ({len(strengths)}):")
        for s in strengths:
            print(f"    + {s}")

    # Print problematic findings
    problems = [f for f in findings
                if f.get("status") in ("MISSING", "INCORRECT", "HALLUCINATED")]
    if problems:
        print(f"  Problems ({len(problems)}):")
        for p in problems:
            print(f"    [{p.get('status')}] {p.get('field')}: {p.get('claim')}")
            print(f"      evidence: {p.get('evidence')}")

    print()
    print(f"  Verdict: {result.get('final_verdict', '')}")
    print()


def _load_test_case(path: str) -> dict:
    """
    Load a test case JSON file.
    Required shape:
      {
        "reel_id": "...",
        "expected_category": "...",
        "caption": "...",
        "transcript": "...",
        "brain_object": { ... }
      }
    """
    p = Path(path)
    if not p.exists():
        print(f"Error: test case file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(p, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"Error: test case file is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(1)

    required = ("reel_id", "expected_category", "caption", "transcript", "brain_object")
    missing  = [k for k in required if k not in data]
    if missing:
        print(f"Error: test case missing required keys: {missing}", file=sys.stderr)
        sys.exit(1)

    return data


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python evaluation/evaluate.py path/to/test_case.json "
            "[--save path/to/output.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    test_case_path = sys.argv[1]

    save_path: str | None = None
    if "--save" in sys.argv:
        idx = sys.argv.index("--save")
        if idx + 1 < len(sys.argv):
            save_path = sys.argv[idx + 1]
        else:
            print("Error: --save requires a file path argument.", file=sys.stderr)
            sys.exit(1)

    case = _load_test_case(test_case_path)

    try:
        result = evaluate_reel(
            reel_id=case["reel_id"],
            expected_category=case["expected_category"],
            caption=case["caption"],
            transcript=case["transcript"],
            brain_object=case["brain_object"],
        )
    except FileNotFoundError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Ollama error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Parse error: {exc}", file=sys.stderr)
        sys.exit(1)
    except EvaluationValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_summary(result)

    if save_path:
        out = Path(save_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Evaluation saved to: {out}")
    else:
        print("(Pass --save path/to/output.json to persist the result.)")


if __name__ == "__main__":
    main()
