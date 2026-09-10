"""
storage/migrate_v2_to_v3.py
---------------------------
One-time (and idempotent) migration tool to import existing ReelForge V2 Brain Objects
from local JSON files (brains/*.json and brains/archive/*.json) into MongoDB Atlas.

Features:
  - Validates each file against ReelForge V2 knowledge schema & category rules.
  - Preserves exact Brain Object schemas without unnecessary transformations.
  - Flags active reels as is_archived=False and archived reels as is_archived=True.
  - Uses bulk upserts (ReplaceOne with upsert=True) to ensure idempotency.
  - Transparently reports progress, skipped invalid files with reasons, and summary stats.
  - Does NOT delete or alter any local JSON files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pymongo import ReplaceOne

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import BRAINS_DIR, CATEGORIES
from storage.brain_object import is_valid_brain_object
from storage.db import get_collection, is_mongo_available, mask_mongo_uri


def _get_validation_failure_reason(data: Any, expected_id: str) -> str:
    """Helper to give a human-readable reason why a file is invalid."""
    if not isinstance(data, dict):
        return "Root is not a valid JSON object/dict"
    reel_id = data.get("id")
    if not reel_id or not isinstance(reel_id, str):
        return f"Missing or invalid 'id' field (found: {reel_id!r})"
    if reel_id != expected_id:
        return f"File stem '{expected_id}' does not match document id '{reel_id}'"
    knowledge = data.get("knowledge")
    if not isinstance(knowledge, dict):
        return "Missing or non-dict 'knowledge' object"
    category = knowledge.get("category")
    if not category or category not in CATEGORIES:
        return f"Category '{category}' is not in valid CATEGORIES list"
    summary = knowledge.get("summary")
    if not summary or not isinstance(summary, str) or not summary.strip():
        return "Missing, null, or empty 'summary' in knowledge"
    try:
        from processing.knowledge_schema import normalize_knowledge_schema
        normalize_knowledge_schema(category, knowledge)
    except Exception as e:
        return f"Schema normalization failed: {e}"
    return "Unknown validation failure"


def run_migration(dry_run: bool = False, verbose: bool = True) -> dict[str, Any]:
    """
    Execute migration from brains/*.json and brains/archive/*.json to MongoDB Atlas.
    """
    if verbose:
        print("==================================================")
        print("ReelForge V2 -> V3 MongoDB Atlas Migration Tool")
        print("==================================================")
        if dry_run:
            print("[DRY-RUN MODE] No data will be written to MongoDB.")

    # 1. Connect to MongoDB Atlas
    if not is_mongo_available():
        raise RuntimeError("MongoDB Atlas is not available or MONGODB_URI is not configured.")

    col = get_collection("brains")
    brains_dir = Path(BRAINS_DIR)
    archive_dir = brains_dir / "archive"

    stats: dict[str, Any] = {
        "total_files_scanned": 0,
        "migrated_active": 0,
        "migrated_archived": 0,
        "skipped_invalid": 0,
        "failed_unreadable": 0,
        "skipped_details": [],
        "failed_details": [],
    }

    bulk_ops: list[ReplaceOne] = []

    # 2. Process Active Files (brains/*.json)
    active_files = [f for f in brains_dir.glob("*.json") if f.is_file()]
    if verbose:
        print(f"\nScanning active library directory: {brains_dir}")
        print(f"Found {len(active_files)} JSON file(s).")

    for file_path in active_files:
        stats["total_files_scanned"] += 1
        expected_id = file_path.stem
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            stats["failed_unreadable"] += 1
            stats["failed_details"].append((file_path.name, f"JSON parse error: {e}"))
            if verbose:
                print(f"  [FAILED] {file_path.name}: Corrupt / Unparseable JSON ({e})")
            continue

        if not is_valid_brain_object(data, expected_reel_id=expected_id):
            reason = _get_validation_failure_reason(data, expected_id)
            stats["skipped_invalid"] += 1
            stats["skipped_details"].append((file_path.name, reason))
            if verbose:
                print(f"  [SKIPPED] {file_path.name}: {reason}")
            continue

        # Prepare Mongo document
        doc = dict(data)
        doc["_id"] = data["id"]
        doc["is_archived"] = False

        bulk_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        stats["migrated_active"] += 1

    # 3. Process Archived Files (brains/archive/*.json)
    archived_files = [f for f in archive_dir.glob("*.json") if f.is_file()] if archive_dir.exists() else []
    if archived_files:
        if verbose:
            print(f"\nScanning archive directory: {archive_dir}")
            print(f"Found {len(archived_files)} archived JSON file(s).")

        for file_path in archived_files:
            stats["total_files_scanned"] += 1
            expected_id = file_path.stem
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                stats["failed_unreadable"] += 1
                stats["failed_details"].append((f"archive/{file_path.name}", f"JSON parse error: {e}"))
                if verbose:
                    print(f"  [FAILED] archive/{file_path.name}: Corrupt / Unparseable JSON ({e})")
                continue

            if not is_valid_brain_object(data, expected_reel_id=expected_id):
                reason = _get_validation_failure_reason(data, expected_id)
                stats["skipped_invalid"] += 1
                stats["skipped_details"].append((f"archive/{file_path.name}", reason))
                if verbose:
                    print(f"  [SKIPPED] archive/{file_path.name}: {reason}")
                continue

            doc = dict(data)
            doc["_id"] = data["id"]
            doc["is_archived"] = True

            bulk_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
            stats["migrated_archived"] += 1

    # 4. Execute Bulk Write
    if bulk_ops and not dry_run:
        if verbose:
            print(f"\nExecuting bulk upsert of {len(bulk_ops)} document(s) to MongoDB Atlas...")
        result = col.bulk_write(bulk_ops, ordered=False)
        if verbose:
            print(
                f"  MongoDB bulk write complete: "
                f"matched={result.matched_count}, "
                f"inserted/upserted={result.upserted_count + result.inserted_count}, "
                f"modified={result.modified_count}"
            )

    # 5. Summary & Verification
    total_in_atlas = col.count_documents({}) if not dry_run else 0
    active_in_atlas = col.count_documents({"is_archived": False}) if not dry_run else 0
    archived_in_atlas = col.count_documents({"is_archived": True}) if not dry_run else 0

    stats["atlas_total_docs"] = total_in_atlas
    stats["atlas_active_docs"] = active_in_atlas
    stats["atlas_archived_docs"] = archived_in_atlas

    if verbose:
        print("\n==================================================")
        print("MIGRATION SUMMARY")
        print("==================================================")
        print(f"Total files scanned:       {stats['total_files_scanned']}")
        print(f"Successfully migrated:     {stats['migrated_active'] + stats['migrated_archived']}")
        print(f"  - Active Reels:          {stats['migrated_active']}")
        print(f"  - Archived Reels:        {stats['migrated_archived']}")
        print(f"Skipped (Incomplete/Null): {stats['skipped_invalid']}")
        print(f"Failed (Unreadable):       {stats['failed_unreadable']}")
        if not dry_run:
            print("--------------------------------------------------")
            print(f"Total Documents in Atlas:  {total_in_atlas}")
            print(f"  - Active:                {active_in_atlas}")
            print(f"  - Archived:              {archived_in_atlas}")
        print("==================================================")

    return stats


def main():
    parser = argparse.ArgumentParser(description="ReelForge V2 to V3 MongoDB Atlas migration tool.")
    parser.add_argument("--dry-run", action="store_true", help="Perform validation and count without writing to MongoDB.")
    args = parser.parse_args()

    try:
        run_migration(dry_run=args.dry_run, verbose=True)
    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
