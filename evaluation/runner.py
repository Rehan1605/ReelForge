"""
evaluation/runner.py
--------------------
Execution and aggregation runner for ReelForge Grounded Evaluation.

Features:
  - Single Reel evaluation: `python -m evaluation.runner --reel <reel_id>`
  - Full active library evaluation: `python -m evaluation.runner --all`
  - Aggregate report inspection: `python -m evaluation.runner --report [run_id]`
  - Persistence under `evaluation/results/<run_id>/`
  - Category, modality, and reprocessing candidate ranking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from config import CATEGORIES, EVALUATION_MODEL, TEXT_MODEL
from evaluation.evaluate import evaluate_brain_object
from storage.brain_object import load_brain_object, scan_valid_brain_objects

ROOT_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT_DIR / "evaluation" / "results"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _format_date_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def save_evaluation_result(run_dir: Path, result: dict) -> Path:
    """Save an individual evaluation result to JSON."""
    reel_id = result.get("reel_id", "unknown")
    out_file = run_dir / f"{reel_id}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_file


def compute_aggregate_report(
    evaluations: list[dict],
    failed: list[dict],
    run_id: str,
    judge_model: str,
) -> dict:
    """Compute aggregate quality metrics across evaluated reels."""
    total_evaluated = len(evaluations)
    scores = [e.get("overall_score", 0.0) for e in evaluations]

    mean_score = round(mean(scores), 1) if scores else 0.0
    median_score = round(median(scores), 1) if scores else 0.0
    min_score = min(scores) if scores else 0.0
    max_score = max(scores) if scores else 0.0

    # Quality bands distribution
    bands = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "critical": 0}
    for e in evaluations:
        band = e.get("quality_band", "poor")
        bands[band] = bands.get(band, 0) + 1

    # Issues breakdown
    issue_counts = {"critical": 0, "major": 0, "minor": 0, "total": 0}
    for e in evaluations:
        for iss in e.get("issues", []):
            sev = iss.get("severity", "minor")
            issue_counts[sev] = issue_counts.get(sev, 0) + 1
            issue_counts["total"] += 1

    # Category breakdown
    category_data = {}
    for cat in CATEGORIES:
        cat_evals = [e for e in evaluations if e.get("category") == cat]
        if not cat_evals:
            category_data[cat] = {
                "count": 0,
                "mean_score": 0.0,
                "median_score": 0.0,
                "lowest_reels": [],
                "common_issue_fields": [],
            }
            continue

        c_scores = [e.get("overall_score", 0.0) for e in cat_evals]
        sorted_cat = sorted(cat_evals, key=lambda x: x.get("overall_score", 0.0))
        lowest_reels = [
            {
                "reel_id": e.get("reel_id"),
                "score": e.get("overall_score"),
                "quality_band": e.get("quality_band"),
            }
            for e in sorted_cat[:3]
        ]

        field_counter: dict[str, int] = {}
        for e in cat_evals:
            for iss in e.get("issues", []):
                f = iss.get("field", "other")
                field_counter[f] = field_counter.get(f, 0) + 1
        common_fields = sorted(field_counter.items(), key=lambda x: -x[1])[:3]

        category_data[cat] = {
            "count": len(cat_evals),
            "mean_score": round(mean(c_scores), 1),
            "median_score": round(median(c_scores), 1),
            "lowest_reels": lowest_reels,
            "common_issue_fields": [cf[0] for cf in common_fields],
        }

    # Modality breakdown
    modality_groups: dict[str, list[float]] = {
        "caption_only": [],
        "caption_transcript": [],
        "caption_vision": [],
        "full_multimodal": [],
    }

    for e in evaluations:
        mods = e.get("available_modalities") or {}
        has_cap = bool(mods.get("caption"))
        has_tra = bool(mods.get("transcript"))
        has_vis = bool(mods.get("vision_analysis"))
        score = e.get("overall_score", 0.0)

        if has_cap and has_tra and has_vis:
            modality_groups["full_multimodal"].append(score)
        elif has_cap and has_tra:
            modality_groups["caption_transcript"].append(score)
        elif has_cap and has_vis:
            modality_groups["caption_vision"].append(score)
        else:
            modality_groups["caption_only"].append(score)

    modality_breakdown = {}
    for mod_name, m_scores in modality_groups.items():
        modality_breakdown[mod_name] = {
            "count": len(m_scores),
            "mean_score": round(mean(m_scores), 1) if m_scores else 0.0,
            "median_score": round(median(m_scores), 1) if m_scores else 0.0,
        }

    # Reprocessing Candidates Ranking
    candidates = []
    for e in evaluations:
        r_id = e.get("reel_id")
        score = e.get("overall_score", 100.0)
        issues = e.get("issues", [])
        has_critical = any(i.get("severity") == "critical" for i in issues)
        has_major = any(i.get("severity") == "major" for i in issues)

        if has_critical or score < 60.0:
            priority = "high"
            reason = issues[0].get("problem") if issues else f"Low overall score ({score})"
        elif has_major or score < 75.0:
            priority = "medium"
            reason = issues[0].get("problem") if issues else f"Moderate score ({score})"
        elif score < 85.0 and issues:
            priority = "low"
            reason = issues[0].get("problem")
        else:
            continue

        candidates.append({
            "reel_id": r_id,
            "category": e.get("category"),
            "score": score,
            "priority": priority,
            "reason": reason,
            "issues_count": len(issues),
        })

    # Sort candidates by priority (high > medium > low) and score ascending
    priority_order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (priority_order.get(c["priority"], 3), c["score"]))

    report = {
        "run_id": run_id,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "judge_model": judge_model,
        "summary": {
            "total_evaluated": total_evaluated,
            "total_failed": len(failed),
            "mean_score": mean_score,
            "median_score": median_score,
            "min_score": min_score,
            "max_score": max_score,
            "quality_bands": bands,
            "issues_summary": issue_counts,
        },
        "by_category": category_data,
        "by_modality": modality_breakdown,
        "reprocessing_candidates": candidates,
        "failed_evaluations": failed,
    }
    return report


def print_aggregate_report(report: dict) -> None:
    """Print a clean, structured terminal summary of an aggregate report."""
    summary = report.get("summary", {})
    run_id = report.get("run_id", "Unknown")
    model = report.get("judge_model", "Unknown")

    print("\n" + "=" * 70)
    print(f"REELFORGE EVALUATION REPORT — Run: {run_id}")
    print(f"Judge Model: {model} | Evaluated At: {report.get('evaluated_at')}")
    print("=" * 70)

    print("\n--- OVERALL QUALITY SUMMARY ---")
    print(f"  Total Evaluated : {summary.get('total_evaluated')}")
    print(f"  Total Failed    : {summary.get('total_failed')}")
    print(f"  Mean Score      : {summary.get('mean_score')}/100")
    print(f"  Median Score    : {summary.get('median_score')}/100")
    print(f"  Score Range     : [{summary.get('min_score')} - {summary.get('max_score')}]")

    print("\n--- QUALITY BAND DISTRIBUTION ---")
    bands = summary.get("quality_bands", {})
    for b_name in ("excellent", "good", "fair", "poor", "critical"):
        cnt = bands.get(b_name, 0)
        pct = (cnt / summary.get('total_evaluated', 1)) * 100 if summary.get('total_evaluated') else 0
        print(f"  {b_name.capitalize():<12}: {cnt:3d} ({pct:5.1f}%)")

    print("\n--- ISSUES SUMMARY ---")
    iss = summary.get("issues_summary", {})
    print(f"  Critical Issues : {iss.get('critical', 0)}")
    print(f"  Major Issues    : {iss.get('major', 0)}")
    print(f"  Minor Issues    : {iss.get('minor', 0)}")
    print(f"  Total Issues    : {iss.get('total', 0)}")

    print("\n--- CATEGORY BREAKDOWN ---")
    print(f"{'Category':<16} | {'Count':<6} | {'Mean':<6} | {'Median':<6} | {'Common Issue Field'}")
    print("-" * 65)
    by_cat = report.get("by_category", {})
    for cat, c_data in by_cat.items():
        cnt = c_data.get("count", 0)
        m_s = c_data.get("mean_score", 0.0)
        med = c_data.get("median_score", 0.0)
        fields = ", ".join(c_data.get("common_issue_fields", [])) or "None"
        print(f"{cat:<16} | {cnt:<6d} | {m_s:<6.1f} | {med:<6.1f} | {fields}")

    print("\n--- MODALITY COMPARISON ---")
    print(f"{'Modality':<22} | {'Count':<6} | {'Mean Score':<10} | {'Median Score'}")
    print("-" * 55)
    by_mod = report.get("by_modality", {})
    for mod_name, m_data in by_mod.items():
        print(f"{mod_name:<22} | {m_data.get('count'):<6d} | {m_data.get('mean_score'):<10.1f} | {m_data.get('median_score'):<6.1f}")

    candidates = report.get("reprocessing_candidates", [])
    print(f"\n--- TOP REPROCESSING CANDIDATES ({len(candidates)} total identified) ---")
    if not candidates:
        print("  None! All reels meet high quality thresholds.")
    else:
        for i, cand in enumerate(candidates[:15], 1):
            pri = cand.get("priority", "medium").upper()
            print(f"  {i:2d}. [{pri:<6}] Reel: `{cand.get('reel_id')}` | Score: {cand.get('score')} | [{cand.get('category')}]")
            print(f"       Reason: {cand.get('reason')}")
    print("=" * 70 + "\n")


def run_full_evaluation(model: str | None = None) -> dict:
    """Evaluate all 74 active valid Brain Objects and persist results."""
    target_model = model or EVALUATION_MODEL or TEXT_MODEL
    run_id = _format_date_id()
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    brains = scan_valid_brain_objects()
    total = len(brains)
    print(f"\n🚀 Starting Full Library Evaluation: {total} active Brain Objects")
    print(f"   Judge Model : {target_model}")
    print(f"   Output Dir  : {run_dir}\n")

    evaluations = []
    failed = []

    for i, brain in enumerate(brains, 1):
        r_id = brain.get("id") or f"unknown_{i}"
        cat = (brain.get("knowledge") or {}).get("category") or "Unknown"
        title = (brain.get("knowledge") or {}).get("title") or r_id
        print(f"[{i:2d}/{total}] Evaluating `{r_id}` [{cat}] '{title[:35]}'...")

        try:
            eval_res = evaluate_brain_object(brain, model=target_model)
            save_evaluation_result(run_dir, eval_res)
            evaluations.append(eval_res)
            score = eval_res.get("overall_score")
            band = eval_res.get("quality_band")
            print(f"       ↳ Score: {score}/100 ({band})")
        except Exception as exc:
            print(f"       ❌ Failed: {exc}")
            failed.append({
                "reel_id": r_id,
                "error": str(exc),
            })

    report = compute_aggregate_report(
        evaluations=evaluations,
        failed=failed,
        run_id=run_id,
        judge_model=target_model,
    )

    # Save aggregate.json in run dir
    agg_file = run_dir / "aggregate.json"
    agg_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update latest aggregate pointer
    latest_file = RESULTS_DIR / "latest_aggregate.json"
    latest_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    latest_id_file = RESULTS_DIR / "latest_run_id.txt"
    latest_id_file.write_text(run_id, encoding="utf-8")

    print_aggregate_report(report)
    print(f"✓ Results persisted under: {run_dir}")
    return report


def run_single_evaluation(reel_id: str, model: str | None = None) -> dict:
    """Evaluate a single specified Reel ID."""
    target_model = model or EVALUATION_MODEL or TEXT_MODEL
    brain = load_brain_object(reel_id)
    if not brain:
        raise FileNotFoundError(f"Active Brain Object for Reel ID '{reel_id}' not found.")

    print(f"\n🔍 Evaluating Reel `{reel_id}` with {target_model}...")
    result = evaluate_brain_object(brain, model=target_model)

    # Print summary card
    print("\n" + "-" * 60)
    print(f"Reel ID      : {result.get('reel_id')}")
    print(f"Category     : {result.get('category')}")
    print(f"Overall Score: {result.get('overall_score')}/100 ({result.get('quality_band')})")
    print("\nDimension Scores:")
    for d_name, d_val in result.get("dimensions", {}).items():
        sc = d_val.get("score")
        app = d_val.get("applicable", True)
        app_str = "" if app else " [NOT APPLICABLE]"
        print(f"  - {d_name:<24}: {sc}{app_str}")
        if d_val.get("reasoning"):
            print(f"    ↳ {d_val.get('reasoning')}")

    issues = result.get("issues", [])
    if issues:
        print(f"\nIssues Identified ({len(issues)}):")
        for iss in issues:
            print(f"  • [{iss.get('severity').upper()}] {iss.get('field')}: {iss.get('problem')}")
            if iss.get("evidence"):
                print(f"    Evidence: {iss.get('evidence')}")
            if iss.get("recommendation"):
                print(f"    Action  : {iss.get('recommendation')}")

    strengths = result.get("strengths", [])
    if strengths:
        print(f"\nStrengths:")
        for s in strengths:
            print(f"  + {s}")
    print("-" * 60 + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="ReelForge Grounded Evaluation Runner")
    parser.add_argument("--reel", type=str, help="Evaluate a single Reel ID")
    parser.add_argument("--all", action="store_true", help="Evaluate all active valid Brain Objects")
    parser.add_argument("--model", type=str, default=None, help="Override evaluation model")
    parser.add_argument("--report", nargs="?", const="latest", default=None, help="Display aggregate report")

    args = parser.parse_args()

    if args.reel:
        run_single_evaluation(args.reel, model=args.model)
    elif args.all:
        run_full_evaluation(model=args.model)
    elif args.report:
        run_id = args.report
        if run_id == "latest":
            latest_file = RESULTS_DIR / "latest_aggregate.json"
            if not latest_file.is_file():
                print("No latest aggregate report found. Run `python -m evaluation.runner --all` first.")
                sys.exit(1)
            report = json.loads(latest_file.read_text(encoding="utf-8"))
        else:
            run_file = RESULTS_DIR / run_id / "aggregate.json"
            if not run_file.is_file():
                print(f"Report for run '{run_id}' not found at '{run_file}'.")
                sys.exit(1)
            report = json.loads(run_file.read_text(encoding="utf-8"))
        print_aggregate_report(report)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
