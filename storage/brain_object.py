"""
storage/brain_object.py
-----------------------
Central Brain Object persistence and query layer for ReelForge.

Supports MongoDB Atlas (ReelForge V3 primary storage) with seamless fallback
to local JSON files (brains/<reel_id>.json) when MongoDB is unavailable or unconfigured.
Preserves 100% of existing function signatures, return types, and deterministic behaviors.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import BRAINS_DIR, CATEGORIES, WORKSPACE_DIR

try:
    from pymongo import ReturnDocument
    from storage.db import get_collection
except ImportError:
    ReturnDocument = None
    get_collection = None

# Global flag to ensure MongoDB collection indexes are initialized only once
_indexes_initialized: bool = False


# ---------------------------------------------------------------------------
# MongoDB Helper & Normalization Functions
# ---------------------------------------------------------------------------

def _ensure_indexes(col: Any) -> None:
    """
    Ensure required performance and query indexes exist on the MongoDB brains collection.
    Executes lazily once per application run.
    """
    global _indexes_initialized
    if _indexes_initialized or col is None:
        return

    try:
        col.create_index([("id", 1)], name="idx_reel_id")
        col.create_index([("is_archived", 1)], name="idx_is_archived")
        col.create_index([("timestamps.processed_at", -1)], name="idx_processed_at")
        col.create_index([("knowledge.category", 1)], name="idx_category")
        col.create_index([("creator.username", 1)], name="idx_creator_username")
        col.create_index([("knowledge.tags", 1)], name="idx_tags")
        col.create_index(
            [("is_archived", 1), ("timestamps.processed_at", -1)],
            name="idx_archived_processed_at",
        )
        _indexes_initialized = True
    except Exception:
        # Non-fatal if index creation fails due to permissions or cluster state
        pass


def _is_custom_brains_dir() -> bool:
    """
    Check if BRAINS_DIR has been monkeypatched/overridden to an isolated temp or mock directory.
    When a custom test directory is detected, bypasses MongoDB Atlas so unit tests operate in local isolation.
    """
    import storage.brain_object as _sbo
    curr_dir = getattr(_sbo, "BRAINS_DIR", BRAINS_DIR)
    curr_str = str(curr_dir).replace("\\", "/").rstrip("/")
    if curr_str != "brains" and not curr_str.endswith("/brains"):
        return True
    return False


def _get_brains_col() -> Any | None:
    """
    Retrieve the MongoDB 'brains' collection instance, or None if unavailable or in test isolation.
    """
    if _is_custom_brains_dir():
        return None
    if get_collection is None:
        return None
    try:
        col = get_collection("brains")
        _ensure_indexes(col)
        return col
    except Exception:
        return None


def _clean_doc(doc: dict | None) -> dict | None:
    """
    Sanitize a MongoDB document dict for consumption:
    - Strips MongoDB internal '_id'
    - Ensures standard 'id' field is present
    - Removes internal indexing flags like 'is_archived' / 'archived_at'
    """
    if not doc or not isinstance(doc, dict):
        return None

    cleaned = dict(doc)
    mongo_id = cleaned.pop("_id", None)
    if not cleaned.get("id") and mongo_id:
        cleaned["id"] = str(mongo_id)

    cleaned.pop("is_archived", None)
    cleaned.pop("archived_at", None)
    return cleaned


# ---------------------------------------------------------------------------
# URL Validation & Shortcode Extraction
# ---------------------------------------------------------------------------

def is_valid_instagram_url(url: str) -> bool:
    """
    Validate whether a given string is a valid Instagram URL.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            return False
        netloc = parsed.netloc.lower()
        if netloc in ("instagram.com", "www.instagram.com", "instagr.am", "m.instagram.com") or netloc.endswith(".instagram.com"):
            return True
        return False
    except Exception:
        return False


def extract_reel_id_from_url(url: str) -> str | None:
    """
    Extract the canonical Instagram reel shortcode/ID from a URL without downloading.
    Handles standard URL formats:
      - https://www.instagram.com/reel/SHORTCODE/
      - https://www.instagram.com/reels/SHORTCODE/
      - https://www.instagram.com/p/SHORTCODE/
      - https://www.instagram.com/share/reel/SHORTCODE/
      - URLs with query params, trailing slashes, or whitespace.
    """
    if not url or not isinstance(url, str):
        return None
    cleaned = url.strip()
    path = cleaned.split("?")[0].split("#")[0].strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    for marker in ("reel", "reels", "p", "tv"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                candidate = parts[idx + 1]
                if candidate:
                    return candidate

    return parts[-1] if parts else None


# ---------------------------------------------------------------------------
# Workspace File Utilities
# ---------------------------------------------------------------------------

def _latest_file(pattern: str) -> Path:
    return max(
        Path(WORKSPACE_DIR).glob(pattern),
        key=lambda f: f.stat().st_mtime
    )


def _latest_thumbnail() -> Path | None:
    thumbnail_patterns = ("*.jpg", "*.jpeg", "*.png", "*.webp")
    thumbnails = []

    for pattern in thumbnail_patterns:
        thumbnails.extend(Path(WORKSPACE_DIR).glob(pattern))

    if not thumbnails:
        return None

    return max(thumbnails, key=lambda f: f.stat().st_mtime)


def _shortcode_from_metadata(source_url: str, metadata: dict) -> str:
    webpage_url = metadata.get("webpage_url") or source_url
    extracted = extract_reel_id_from_url(webpage_url)
    if extracted:
        return extracted

    parts = [p for p in webpage_url.strip("/").split("/") if p]
    if parts:
        return parts[-1]

    return metadata.get("id")


# ---------------------------------------------------------------------------
# Validation & Atomic File IO
# ---------------------------------------------------------------------------

def is_valid_brain_object(brain: dict | None, expected_reel_id: str | None = None) -> bool:
    """
    Check if a Brain Object represents a fully completed, valid knowledge extraction.
    Must have matching reel_id, valid category, non-empty summary, and valid schema.
    """
    if not isinstance(brain, dict):
        return False

    reel_id = brain.get("id")
    if not reel_id or not isinstance(reel_id, str):
        return False

    if expected_reel_id is not None and reel_id != expected_reel_id:
        return False

    knowledge = brain.get("knowledge")
    if not isinstance(knowledge, dict):
        return False

    category = knowledge.get("category")
    if not category or category not in CATEGORIES:
        return False

    summary = knowledge.get("summary")
    if not summary or not isinstance(summary, str) or not summary.strip():
        return False

    try:
        from processing.knowledge_schema import normalize_knowledge_schema
        normalize_knowledge_schema(category, knowledge)
    except Exception:
        return False

    return True


def _atomic_write_json(file_path: Path, data: dict) -> None:
    """
    Atomically write JSON data to file_path using a temporary file in the same directory.
    Prevents partial/corrupted files if interrupted.
    """
    target = Path(file_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f"{target.stem}_tmp_",
        suffix=".json",
    )
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(temp_path, target)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Cache Lookup
# ---------------------------------------------------------------------------

def get_cached_brain_object(reel_id: str) -> dict | None:
    """
    Retrieve an existing Brain Object by reel_id if it is fully valid and completed.
    Checks MongoDB Atlas primary first, then falls back to local JSON cache.
    Returns the loaded dict on cache hit, or None on cache miss / partial / malformed.
    """
    if not reel_id or not isinstance(reel_id, str):
        return None

    # 1. Check MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one({"_id": reel_id, "is_archived": {"$ne": True}})
            if doc is None:
                doc = col.find_one({"id": reel_id, "is_archived": {"$ne": True}})
            if doc:
                clean = _clean_doc(doc)
                if is_valid_brain_object(clean, expected_reel_id=reel_id):
                    return clean
    except Exception:
        pass

    # 2. Fallback to Local JSON Storage
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if not brain_path.is_file():
        return None

    try:
        with open(brain_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if is_valid_brain_object(data, expected_reel_id=reel_id):
            return data
    except Exception:
        return None

    return None


# ---------------------------------------------------------------------------
# Object Construction & Provenance
# ---------------------------------------------------------------------------

def create_brain_object(source_url: str, metadata: dict | None = None) -> dict:
    """
    Initialize a new Brain Object structure from downloaded Reel metadata.
    Persists to MongoDB Atlas (primary) and creates a local JSON backup.
    """
    if metadata is None:
        metadata_file = _latest_file("*.info.json")
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    shortcode = _shortcode_from_metadata(source_url, metadata)
    reel_id = metadata.get("id") or shortcode
    caption = metadata.get("description") or ""

    target_mp4 = Path(WORKSPACE_DIR) / f"{reel_id}.mp4"
    if target_mp4.is_file():
        mp4_file = target_mp4
    else:
        mp4_file = _latest_file("*.mp4")

    thumbnail_file = None
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = Path(WORKSPACE_DIR) / f"{reel_id}.{ext}"
        if candidate.is_file():
            thumbnail_file = candidate
            break
    if not thumbnail_file:
        thumbnail_file = _latest_thumbnail()

    reel = {
        "id": reel_id,
        "status": "downloaded",
        "source": {
            "platform": "instagram",
            "url": metadata.get("webpage_url") or source_url,
            "shortcode": shortcode
        },
        "creator": {
            "username": metadata.get("uploader"),
            "full_name": metadata.get("uploader"),
            "verified": None,
            "followers": None
        },
        "metrics": {
            "views": metadata.get("view_count"),
            "plays": metadata.get("view_count")
        },
        "content": {
            "caption": caption,
            "transcript": None,
            "vision_analysis": None
        },
        "media": {
            "video_path": str(mp4_file),
            "thumbnail_path": str(thumbnail_file) if thumbnail_file else metadata.get("thumbnail"),
            "duration": metadata.get("duration")
        },
        "knowledge": {
            "category": None,
            "summary": None,
            "tags": [],
            "structured_data": None
        },
        "timestamps": {
            "reel_created": metadata.get("upload_date"),
            "processed_at": datetime.now().isoformat(timespec="seconds")
        },
        "provenance": {
            "extraction_version": "2.6.0",
            "modality": "caption_only",
            "has_audio_transcript": False,
            "has_vision_analysis": False
        }
    }

    # 1. Persist to MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            mongo_doc = dict(reel)
            mongo_doc["_id"] = reel_id
            mongo_doc["is_archived"] = False
            col.replace_one({"_id": reel_id}, mongo_doc, upsert=True)
    except Exception as e:
        print(f"Notice: Could not persist Brain Object to MongoDB: {e}")

    # 2. Persist to Local File as Backup
    try:
        Path(BRAINS_DIR).mkdir(exist_ok=True)
        output = Path(BRAINS_DIR) / f"{reel_id}.json"
        _atomic_write_json(output, reel)
        print(f"Master JSON Created: {output}")
    except Exception as e:
        print(f"Notice: Could not persist local JSON backup: {e}")

    return reel


def compute_provenance(
    transcript: str | None = None,
    vision_analysis: dict | list | None = None,
) -> dict:
    """
    Deterministically build provenance metadata based on actual available data.
    """
    has_audio = bool(transcript and isinstance(transcript, str) and transcript.strip())

    has_vision = False
    if isinstance(vision_analysis, dict):
        has_vision = any(bool(v) for v in vision_analysis.values())
    elif isinstance(vision_analysis, list):
        has_vision = bool(vision_analysis)

    if has_audio and has_vision:
        modality = "multimodal"
    elif has_audio:
        modality = "audio_caption"
    elif has_vision:
        modality = "vision_caption"
    else:
        modality = "caption_only"

    return {
        "extraction_version": "2.6.0",
        "modality": modality,
        "has_audio_transcript": has_audio,
        "has_vision_analysis": has_vision,
    }


# ---------------------------------------------------------------------------
# Incremental Pipeline Updates (Atomic MongoDB + Local Sync)
# ---------------------------------------------------------------------------

def update_transcript(reel_id: str, transcript: str) -> dict:
    """
    Persist the audio transcript into the Brain Object content section.
    """
    updated_brain: dict | None = None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one_and_update(
                {"_id": reel_id},
                {"$set": {"content.transcript": transcript}},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
            if doc is None:
                doc = col.find_one_and_update(
                    {"id": reel_id},
                    {"$set": {"content.transcript": transcript}},
                    return_document=ReturnDocument.AFTER if ReturnDocument else True,
                )
            if doc:
                updated_brain = _clean_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB transcript update failed: {e}")

    # 2. Update Local File Backup
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                local_brain = json.load(f)
            if "content" not in local_brain or not isinstance(local_brain["content"], dict):
                local_brain["content"] = {}
            local_brain["content"]["transcript"] = transcript
            _atomic_write_json(brain_path, local_brain)
            if updated_brain is None:
                updated_brain = local_brain
        except Exception as e:
            print(f"Notice: Local transcript file sync failed: {e}")

    if updated_brain is not None:
        return updated_brain

    raise FileNotFoundError(f"Brain Object '{reel_id}' not found in MongoDB or local storage.")


def update_vision_analysis(reel_id: str, vision_analysis: dict) -> dict:
    """
    Persist visual analysis into the Brain Object content section.
    """
    updated_brain: dict | None = None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one_and_update(
                {"_id": reel_id},
                {"$set": {"content.vision_analysis": vision_analysis}},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
            if doc is None:
                doc = col.find_one_and_update(
                    {"id": reel_id},
                    {"$set": {"content.vision_analysis": vision_analysis}},
                    return_document=ReturnDocument.AFTER if ReturnDocument else True,
                )
            if doc:
                updated_brain = _clean_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB vision update failed: {e}")

    # 2. Update Local File Backup
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                local_brain = json.load(f)
            if "content" not in local_brain or not isinstance(local_brain["content"], dict):
                local_brain["content"] = {}
            local_brain["content"]["vision_analysis"] = vision_analysis
            _atomic_write_json(brain_path, local_brain)
            if updated_brain is None:
                updated_brain = local_brain
        except Exception as e:
            print(f"Notice: Local vision file sync failed: {e}")

    if updated_brain is not None:
        return updated_brain

    raise FileNotFoundError(f"Brain Object '{reel_id}' not found in MongoDB or local storage.")


def update_latest_vision_analysis(vision_analysis: dict) -> dict:
    latest = load_latest_brain_object()
    return update_vision_analysis(latest["id"], vision_analysis)


def update_category(reel_id: str, category: str) -> dict:
    """
    Persist categorized domain into the Brain Object knowledge section.
    """
    updated_brain: dict | None = None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one_and_update(
                {"_id": reel_id},
                {"$set": {"knowledge.category": category}},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
            if doc is None:
                doc = col.find_one_and_update(
                    {"id": reel_id},
                    {"$set": {"knowledge.category": category}},
                    return_document=ReturnDocument.AFTER if ReturnDocument else True,
                )
            if doc:
                updated_brain = _clean_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB category update failed: {e}")

    # 2. Update Local File Backup
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                local_brain = json.load(f)
            if "knowledge" not in local_brain or not isinstance(local_brain["knowledge"], dict):
                local_brain["knowledge"] = {}
            local_brain["knowledge"]["category"] = category
            _atomic_write_json(brain_path, local_brain)
            if updated_brain is None:
                updated_brain = local_brain
        except Exception as e:
            print(f"Notice: Local category file sync failed: {e}")

    if updated_brain is not None:
        return updated_brain

    raise FileNotFoundError(f"Brain Object '{reel_id}' not found in MongoDB or local storage.")


def update_latest_category(category: str) -> dict:
    latest = load_latest_brain_object()
    return update_category(latest["id"], category)


def update_knowledge(reel_id: str, knowledge: dict) -> dict:
    """
    Update the knowledge section of a specific Brain Object by reel ID.
    Preserves existing category if newly extracted schema category is empty.
    """
    if not knowledge:
        print("Extractor returned no output; keeping existing Brain Object knowledge.")
        return load_brain_object(reel_id)

    updated_brain: dict | None = None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            existing_doc = col.find_one({"_id": reel_id}) or col.find_one({"id": reel_id})
            existing_cat = (existing_doc.get("knowledge") or {}).get("category") if existing_doc else None

            knowledge_to_save = dict(knowledge)
            if existing_cat and not knowledge_to_save.get("category"):
                knowledge_to_save["category"] = existing_cat

            doc = col.find_one_and_update(
                {"_id": reel_id},
                {"$set": {"knowledge": knowledge_to_save}},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
            if doc is None:
                doc = col.find_one_and_update(
                    {"id": reel_id},
                    {"$set": {"knowledge": knowledge_to_save}},
                    return_document=ReturnDocument.AFTER if ReturnDocument else True,
                )
            if doc:
                updated_brain = _clean_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB knowledge update failed: {e}")

    # 2. Update Local File Backup
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                local_brain = json.load(f)
            existing_cat = (local_brain.get("knowledge") or {}).get("category")
            knowledge_to_save = dict(knowledge)
            if existing_cat and not knowledge_to_save.get("category"):
                knowledge_to_save["category"] = existing_cat

            local_brain["knowledge"] = knowledge_to_save
            _atomic_write_json(brain_path, local_brain)
            if updated_brain is None:
                updated_brain = local_brain
        except Exception as e:
            print(f"Notice: Local knowledge file sync failed: {e}")

    if updated_brain is not None:
        return updated_brain

    raise FileNotFoundError(f"Brain Object '{reel_id}' not found in MongoDB or local storage.")


def update_latest_knowledge(knowledge: dict) -> dict:
    latest = load_latest_brain_object()
    return update_knowledge(latest["id"], knowledge)


def update_latest_programming_knowledge(knowledge: dict) -> dict:
    return update_latest_knowledge(knowledge)


def update_provenance(reel_id: str, provenance: dict) -> dict:
    """
    Update the root provenance metadata of a specific Brain Object by reel ID.
    """
    updated_brain: dict | None = None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one_and_update(
                {"_id": reel_id},
                {"$set": {"provenance": provenance}},
                return_document=ReturnDocument.AFTER if ReturnDocument else True,
            )
            if doc is None:
                doc = col.find_one_and_update(
                    {"id": reel_id},
                    {"$set": {"provenance": provenance}},
                    return_document=ReturnDocument.AFTER if ReturnDocument else True,
                )
            if doc:
                updated_brain = _clean_doc(doc)
    except Exception as e:
        print(f"Notice: MongoDB provenance update failed: {e}")

    # 2. Update Local File Backup
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        try:
            with open(brain_path, "r", encoding="utf-8") as f:
                local_brain = json.load(f)
            local_brain["provenance"] = provenance
            _atomic_write_json(brain_path, local_brain)
            if updated_brain is None:
                updated_brain = local_brain
        except Exception as e:
            print(f"Notice: Local provenance file sync failed: {e}")

    if updated_brain is not None:
        return updated_brain

    raise FileNotFoundError(f"Brain Object '{reel_id}' not found in MongoDB or local storage.")


# ---------------------------------------------------------------------------
# Loading & Scanning
# ---------------------------------------------------------------------------

def load_brain_object(reel_id: str) -> dict:
    """
    Load a specific Brain Object by its reel ID.
    Checks MongoDB Atlas first, then local brains/<reel_id>.json.
    """
    # 1. Check MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one({"_id": reel_id})
            if doc is None:
                doc = col.find_one({"id": reel_id})
            if doc:
                return _clean_doc(doc)
    except Exception:
        pass

    # 2. Check Local File
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if brain_path.exists():
        with open(brain_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 3. Check Archived Local File
    archive_path = Path(BRAINS_DIR) / "archive" / f"{reel_id}.json"
    if archive_path.exists():
        with open(archive_path, "r", encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError(f"Brain Object not found: {reel_id}")


def load_latest_brain_object() -> dict:
    """
    Load the most recently processed Brain Object.
    """
    # 1. Check MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one(
                {"is_archived": {"$ne": True}},
                sort=[("timestamps.processed_at", -1)]
            )
            if doc:
                return _clean_doc(doc)
    except Exception:
        pass

    # 2. Check Local Files
    brains_dir = Path(BRAINS_DIR)
    json_files = list(brains_dir.glob("*.json"))
    if json_files:
        latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
        return load_brain_object(latest_file.stem)

    raise FileNotFoundError("No Brain Objects found in storage.")


def scan_valid_brain_objects() -> list[dict]:
    """
    Scan and load all fully valid Brain Objects across the active library.
    Safely ignores malformed, partial, or corrupted records.
    Prioritizes MongoDB Atlas; falls back to local JSON files if MongoDB has no active docs.
    """
    # 1. Query MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            cursor = col.find({"is_archived": {"$ne": True}})
            mongo_valid = []
            for doc in cursor:
                clean = _clean_doc(doc)
                if is_valid_brain_object(clean):
                    mongo_valid.append(clean)
            if mongo_valid:
                return mongo_valid
    except Exception:
        pass

    # 2. Fallback to Local JSON Storage (V2 compatibility before migration)
    brains_dir = Path(BRAINS_DIR)
    if not brains_dir.exists():
        return []

    valid_brains = []
    for json_file in brains_dir.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if is_valid_brain_object(data):
                valid_brains.append(data)
        except Exception:
            continue

    return valid_brains


def _brain_sort_key(brain: dict) -> str:
    """Extract a standardized chronological sort key for a Brain Object."""
    timestamps = brain.get("timestamps") or {}
    processed_at = timestamps.get("processed_at")
    if isinstance(processed_at, str) and processed_at.strip():
        return processed_at.strip()
    reel_created = timestamps.get("reel_created")
    if isinstance(reel_created, str) and reel_created.strip():
        return reel_created.strip()
    return ""


def get_recent_brain_objects(limit: int = 5) -> list[dict]:
    """
    Retrieve valid Brain Objects sorted by processed timestamp descending.
    Limit is clamped between 1 and 10 (default 5).
    """
    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 5
    clamped_limit = max(1, min(limit_val, 10))

    valid_brains = scan_valid_brain_objects()
    valid_brains.sort(key=_brain_sort_key, reverse=True)
    return valid_brains[:clamped_limit]


# ---------------------------------------------------------------------------
# Search & Categorization
# ---------------------------------------------------------------------------

def _extract_searchable_text(brain: dict) -> str:
    """
    Extract useful knowledge and metadata text for deterministic search matching.
    Includes title, summary, tags, topic, key concepts, creator, and category-specific fields.
    """
    k = brain.get("knowledge") or {}
    c = brain.get("content") or {}
    cr = brain.get("creator") or {}
    src = brain.get("source") or {}
    parts = []

    for s in (
        k.get("title"),
        k.get("summary"),
        k.get("main_topic"),
        k.get("category"),
        cr.get("username"),
        cr.get("full_name"),
        c.get("caption"),
        c.get("transcript"),
        src.get("shortcode"),
        brain.get("id"),
    ):
        if s and isinstance(s, str):
            parts.append(s.lower())

    list_keys = (
        "tags", "key_concepts", "key_takeaways", "tools", "apps", "models",
        "websites", "dishes", "ingredients", "cuisine", "cookware", "steps",
        "exercises", "muscles", "equipment", "workout_type", "form_cues",
        "destinations", "attractions", "hotels", "restaurants", "transport",
        "methods", "workflows", "habits", "shortcuts", "stocks", "funds",
        "movies", "shows", "songs", "gear", "techniques", "editing_tools",
        "editing_apps", "transitions", "effects", "best_practices",
        "mistakes_to_avoid", "action_items", "code_snippets", "prompts",
        "use_cases", "key_points", "tips", "recommendations", "risks"
    )
    for key in list_keys:
        val = k.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item:
                    parts.append(item.lower())
                elif isinstance(item, dict):
                    for dv in item.values():
                        if isinstance(dv, str) and dv:
                            parts.append(dv.lower())

    return " ".join(parts)


def search_brain_objects(query: str = "", category: str | None = None, limit: int = 5) -> list[dict]:
    """
    Perform deterministic, case-insensitive keyword search across valid Brain Objects.
    If category is supplied, filters by normalized category first.
    Limit is clamped between 1 and 10 (default 5).
    """
    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 5
    clamped_limit = max(1, min(limit_val, 10))

    valid_brains = scan_valid_brain_objects()

    # Category filter
    if category and isinstance(category, str) and category.strip():
        target_cat = category.strip().lower()
        valid_brains = [
            b for b in valid_brains
            if ((b.get("knowledge") or {}).get("category") or "").strip().lower() == target_cat
        ]

    # Keyword filter
    cleaned_query = (query or "").strip().lower()
    if cleaned_query:
        tokens = [t for t in cleaned_query.split() if t]
        matched = []
        for brain in valid_brains:
            searchable_text = _extract_searchable_text(brain)
            if all(token in searchable_text for token in tokens):
                matched.append(brain)
        valid_brains = matched

    valid_brains.sort(key=_brain_sort_key, reverse=True)
    return valid_brains[:clamped_limit]


def get_brain_categories() -> dict[str, int]:
    """
    Return a category-to-count mapping for all valid Brain Objects.
    Preserves config CATEGORIES ordering.
    """
    valid_brains = scan_valid_brain_objects()
    counts = {cat: 0 for cat in CATEGORIES}

    for brain in valid_brains:
        cat = (brain.get("knowledge") or {}).get("category") or "Other"
        if cat in counts:
            counts[cat] += 1
        else:
            counts[cat] = 1

    return counts


def get_knowledge_stats() -> dict:
    """
    Aggregate statistics across all valid Brain Objects in the knowledge base.
    """
    valid_brains = scan_valid_brain_objects()
    category_counts = get_brain_categories()

    unique_tags = set()
    for brain in valid_brains:
        tags = (brain.get("knowledge") or {}).get("tags") or []
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                unique_tags.add(tag.strip().lower())

    latest_brain = max(valid_brains, key=_brain_sort_key) if valid_brains else None
    latest_ts = None
    latest_id = None
    latest_title = None

    if latest_brain:
        latest_ts = _brain_sort_key(latest_brain) or None
        latest_id = latest_brain.get("id")
        latest_title = (latest_brain.get("knowledge") or {}).get("title")

    return {
        "total_reels": len(valid_brains),
        "category_counts": category_counts,
        "unique_tags_count": len(unique_tags),
        "latest_processed_at": latest_ts,
        "latest_reel_id": latest_id,
        "latest_title": latest_title,
    }


# ---------------------------------------------------------------------------
# Cross-Reel Knowledge Linking & Topic Discovery
# ---------------------------------------------------------------------------

WEIGHT_SHARED_TAG = 3
WEIGHT_SHARED_TOOL = 2
WEIGHT_SAME_CREATOR = 2
WEIGHT_SAME_CATEGORY = 1
MIN_RELATED_SCORE = 2


def _extract_tags_set(brain: dict) -> set[str]:
    k = brain.get("knowledge") or {}
    tags = k.get("tags") or []
    out = set()
    for t in tags:
        if isinstance(t, str) and t.strip():
            out.add(t.strip().lower().lstrip("#"))
    main_topic = k.get("main_topic")
    if isinstance(main_topic, str) and main_topic.strip():
        out.add(main_topic.strip().lower().lstrip("#"))
    return out


def _extract_tools_set(brain: dict) -> set[str]:
    k = brain.get("knowledge") or {}
    out = set()
    for key in ("tools", "apps", "websites", "models", "editing_apps", "gear", "cookware", "equipment"):
        val = k.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    out.add(item.strip().lower())
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str) and v.strip():
                            out.add(v.strip().lower())
    return out


def _extract_creator_normalized(brain: dict) -> str | None:
    cr = brain.get("creator") or {}
    username = cr.get("username")
    if username and isinstance(username, str) and username.strip():
        return username.strip().lower().lstrip("@")
    return None


def find_related_brain_objects(
    reel_id: str,
    limit: int = 5,
) -> list[dict]:
    """
    Deterministically find active Brain Objects related to the specified reel_id.
    Excludes the target reel itself and all archived reels.
    Returns a list of dicts with keys:
      - 'brain': The matched Brain Object dict
      - 'score': The deterministic relevance score
      - 'reasons': List of human-readable match reasons
      - 'shared_tags': List of shared tag strings
      - 'shared_tools': List of shared tool strings
    """
    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 5
    clamped_limit = max(1, min(limit_val, 10))

    if not reel_id or not isinstance(reel_id, str):
        return []

    active_brains = scan_valid_brain_objects()
    target_brain = None
    candidates = []

    for b in active_brains:
        if b.get("id") == reel_id:
            target_brain = b
        else:
            candidates.append(b)

    if not target_brain:
        return []

    target_tags = _extract_tags_set(target_brain)
    target_tools = _extract_tools_set(target_brain)
    target_creator = _extract_creator_normalized(target_brain)
    target_cat = ((target_brain.get("knowledge") or {}).get("category") or "").strip().lower()

    matches = []

    for candidate in candidates:
        cand_tags = _extract_tags_set(candidate)
        cand_tools = _extract_tools_set(candidate)
        cand_creator = _extract_creator_normalized(candidate)
        cand_cat = ((candidate.get("knowledge") or {}).get("category") or "").strip().lower()

        shared_tags = sorted(list(target_tags.intersection(cand_tags)))
        shared_tools = sorted(list(target_tools.intersection(cand_tools)))
        same_creator = bool(target_creator and cand_creator and target_creator == cand_creator)
        same_category = bool(target_cat and cand_cat and target_cat == cand_cat)

        score = (
            WEIGHT_SHARED_TAG * len(shared_tags)
            + WEIGHT_SHARED_TOOL * len(shared_tools)
            + (WEIGHT_SAME_CREATOR if same_creator else 0)
            + (WEIGHT_SAME_CATEGORY if same_category else 0)
        )

        if score < MIN_RELATED_SCORE:
            continue

        reasons = []
        if shared_tags:
            reasons.append(f"Shared tags: {', '.join('#' + t for t in shared_tags)}")
        if shared_tools:
            reasons.append(f"Shared tools: {', '.join(shared_tools)}")
        if same_creator:
            cr_display = (candidate.get("creator") or {}).get("username") or cand_creator
            reasons.append(f"Same creator: @{cr_display}")
        if same_category and (shared_tags or shared_tools or same_creator):
            cat_display = (candidate.get("knowledge") or {}).get("category")
            reasons.append(f"Category: {cat_display}")

        matches.append({
            "brain": candidate,
            "score": score,
            "reasons": reasons,
            "shared_tags": shared_tags,
            "shared_tools": shared_tools,
            "same_creator": same_creator,
            "same_category": same_category,
        })

    matches.sort(
        key=lambda m: (
            -m["score"],
            -len(m["shared_tags"]),
            m["brain"].get("id") or ""
        )
    )

    return matches[:clamped_limit]


def get_all_topics() -> list[tuple[str, int]]:
    """
    Return all unique tags/topics across active valid Brain Objects with reel counts.
    """
    valid_brains = scan_valid_brain_objects()
    counts: dict[str, int] = {}

    for brain in valid_brains:
        tags = _extract_tags_set(brain)
        for t in tags:
            counts[t] = counts.get(t, 0) + 1

    sorted_topics = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0])
    )
    return sorted_topics


def get_brain_objects_by_topic(topic: str, limit: int = 10) -> list[dict]:
    """
    Find all active Brain Objects tagged with or matching topic.
    """
    if not topic or not isinstance(topic, str):
        return []

    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 10
    clamped_limit = max(1, min(limit_val, 20))

    cleaned_topic = topic.strip().lower().lstrip("#")
    if not cleaned_topic:
        return []

    valid_brains = scan_valid_brain_objects()
    matched = []

    for brain in valid_brains:
        tags = _extract_tags_set(brain)
        if cleaned_topic in tags:
            matched.append(brain)

    matched.sort(
        key=lambda b: (
            _brain_sort_key(b) or "",
            b.get("id") or ""
        ),
        reverse=True
    )
    return matched[:clamped_limit]


def get_brain_objects_by_creator(creator: str, limit: int = 10) -> list[dict]:
    """
    Find all active Brain Objects by creator username or full_name.
    """
    if not creator or not isinstance(creator, str):
        return []

    try:
        limit_val = int(limit)
    except (ValueError, TypeError):
        limit_val = 10
    clamped_limit = max(1, min(limit_val, 20))

    cleaned_creator = creator.strip().lower().lstrip("@")
    if not cleaned_creator:
        return []

    valid_brains = scan_valid_brain_objects()
    matched = []

    for brain in valid_brains:
        cand_user = _extract_creator_normalized(brain)
        cr = brain.get("creator") or {}
        full_name = (cr.get("full_name") or "").strip().lower().lstrip("@")
        if cand_user == cleaned_creator or full_name == cleaned_creator:
            matched.append(brain)

    matched.sort(
        key=lambda b: (
            _brain_sort_key(b) or "",
            b.get("id") or ""
        ),
        reverse=True
    )
    return matched[:clamped_limit]


# ---------------------------------------------------------------------------
# Lifecycle Management (Archive / Restore / Recategorize)
# ---------------------------------------------------------------------------

def archive_brain_object(reel_id: str) -> tuple[bool, str]:
    """
    Archive a Brain Object (sets is_archived=True in MongoDB, moves to brains/archive/ locally).
    Excludes it from discovery commands, stats, and cache lookups.
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided."

    archived_in_mongo = False

    # 1. Archive in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one({"_id": reel_id}) or col.find_one({"id": reel_id})
            if doc:
                if doc.get("is_archived") is True:
                    return False, f"Archive collision: '{reel_id}' already exists in archive."
                col.update_one(
                    {"_id": doc.get("_id", reel_id)},
                    {"$set": {"is_archived": True, "archived_at": datetime.now().isoformat()}}
                )
                archived_in_mongo = True
    except Exception as e:
        print(f"Notice: MongoDB archive operation failed: {e}")

    # 2. Archive Local File Backup
    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    archive_dir = Path(BRAINS_DIR) / "archive"
    archive_path = archive_dir / f"{reel_id}.json"

    if active_path.is_file():
        if archive_path.is_file():
            if not archived_in_mongo:
                return False, f"Archive collision: '{reel_id}' already exists in archive."
        else:
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                os.replace(active_path, archive_path)
            except Exception as e:
                if not archived_in_mongo:
                    return False, f"Failed to archive Reel '{reel_id}': {e}"

    if archived_in_mongo or archive_path.is_file():
        return True, f"Reel '{reel_id}' successfully archived."

    return False, f"Reel '{reel_id}' not found in active library."


def restore_brain_object(reel_id: str) -> tuple[bool, str]:
    """
    Restore an archived Brain Object (sets is_archived=False in MongoDB, moves back to brains/ locally).
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided."

    restored_in_mongo = False

    # 1. Restore in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            doc = col.find_one({"_id": reel_id}) or col.find_one({"id": reel_id})
            if doc:
                if not doc.get("is_archived"):
                    return False, f"Restore collision: Reel '{reel_id}' already exists in active library."
                col.update_one(
                    {"_id": doc.get("_id", reel_id)},
                    {"$set": {"is_archived": False}, "$unset": {"archived_at": ""}}
                )
                restored_in_mongo = True
    except Exception as e:
        print(f"Notice: MongoDB restore operation failed: {e}")

    # 2. Restore Local File Backup
    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    archive_path = Path(BRAINS_DIR) / "archive" / f"{reel_id}.json"

    if archive_path.is_file():
        if active_path.is_file():
            if not restored_in_mongo:
                return False, f"Restore collision: Reel '{reel_id}' already exists in active library."
        else:
            try:
                Path(BRAINS_DIR).mkdir(parents=True, exist_ok=True)
                os.replace(archive_path, active_path)
            except Exception as e:
                if not restored_in_mongo:
                    return False, f"Failed to restore Reel '{reel_id}': {e}"

    if restored_in_mongo or active_path.is_file():
        return True, f"Reel '{reel_id}' successfully restored to active library."

    return False, f"Archived Reel '{reel_id}' not found in archive."


def recategorize_brain_object(reel_id: str, new_category: str) -> tuple[bool, str, dict | None]:
    """
    Manually update category and normalize knowledge schema without invoking an LLM.
    Preserves summary, title, tags, and all valid existing knowledge fields.
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided.", None

    if not new_category or not isinstance(new_category, str):
        return False, "Invalid category provided.", None

    matched_category = None
    for cat in CATEGORIES:
        if cat.lower() == new_category.strip().lower():
            matched_category = cat
            break

    if not matched_category:
        valid_list = ", ".join(CATEGORIES)
        return False, f"Unknown category '{new_category}'. Valid categories are:\n{valid_list}", None

    try:
        brain = load_brain_object(reel_id)
    except Exception as e:
        return False, f"Reel '{reel_id}' not found in active library: {e}", None

    if "knowledge" not in brain or not isinstance(brain["knowledge"], dict):
        brain["knowledge"] = {"category": matched_category, "summary": "No summary available."}
    else:
        brain["knowledge"]["category"] = matched_category

    try:
        from processing.knowledge_schema import normalize_knowledge_schema
        normalized = normalize_knowledge_schema(matched_category, brain["knowledge"])
        brain["knowledge"] = normalized
    except Exception as e:
        return False, f"Schema normalization failed for category '{matched_category}': {e}", None

    # 1. Update in MongoDB Atlas
    try:
        col = _get_brains_col()
        if col is not None:
            col.update_one(
                {"_id": reel_id},
                {"$set": {"knowledge": normalized}},
                upsert=False
            )
    except Exception as e:
        print(f"Notice: MongoDB recategorize update failed: {e}")

    # 2. Update Local File Backup
    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if active_path.is_file():
        try:
            _atomic_write_json(active_path, brain)
        except Exception as e:
            print(f"Notice: Local recategorize file sync failed: {e}")

    return True, f"Category updated to '{matched_category}'.", brain
