import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from config import BRAINS_DIR, CATEGORIES, WORKSPACE_DIR
from urllib.parse import urlparse


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


def _latest_file(pattern):
    return max(
        Path(WORKSPACE_DIR).glob(pattern),
        key=lambda f: f.stat().st_mtime
    )


def _latest_thumbnail():
    thumbnail_patterns = ("*.jpg", "*.jpeg", "*.png", "*.webp")
    thumbnails = []

    for pattern in thumbnail_patterns:
        thumbnails.extend(Path(WORKSPACE_DIR).glob(pattern))

    if not thumbnails:
        return None

    return max(thumbnails, key=lambda f: f.stat().st_mtime)


def _shortcode_from_metadata(source_url, metadata):
    webpage_url = metadata.get("webpage_url") or source_url
    extracted = extract_reel_id_from_url(webpage_url)
    if extracted:
        return extracted

    parts = [p for p in webpage_url.strip("/").split("/") if p]

    if parts:
        return parts[-1]

    return metadata.get("id")


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


def get_cached_brain_object(reel_id: str) -> dict | None:
    """
    Retrieve an existing Brain Object by reel_id if it is fully valid and completed.
    Returns the loaded dict on cache hit, or None on cache miss / partial / malformed.
    """
    if not reel_id or not isinstance(reel_id, str):
        return None

    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if not brain_path.is_file():
        return None

    try:
        with open(brain_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if is_valid_brain_object(data, expected_reel_id=reel_id):
        return data

    return None


def _atomic_write_json(file_path: Path, data: dict):
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


def create_brain_object(source_url, metadata=None):
    if metadata is None:
        metadata_file = _latest_file("*.info.json")

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    shortcode = _shortcode_from_metadata(source_url, metadata)
    reel_id = metadata.get("id") or shortcode
    caption = metadata.get("description") or ""

    # Locate reel-specific media if available, fallback to latest
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

    # Build Master JSON
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

    # Save atomically
    Path(BRAINS_DIR).mkdir(exist_ok=True)
    output = Path(BRAINS_DIR) / f"{reel_id}.json"
    _atomic_write_json(output, reel)

    print(f"Master JSON Created: {output}")

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


def update_transcript(reel_id: str, transcript: str) -> dict:
    """
    Persist the audio transcript into the Brain Object content section.
    """
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    if not brain_path.exists():
        raise FileNotFoundError(f"Brain Object not found: {brain_path}")

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    if "content" not in brain or not isinstance(brain["content"], dict):
        brain["content"] = {}

    brain["content"]["transcript"] = transcript
    _atomic_write_json(brain_path, brain)

    return brain


def update_provenance(reel_id: str, provenance: dict) -> dict:
    """
    Update the root provenance metadata of a specific Brain Object by reel ID.
    """
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    if not brain_path.exists():
        raise FileNotFoundError(f"Brain Object not found: {brain_path}")

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    brain["provenance"] = provenance
    _atomic_write_json(brain_path, brain)

    return brain


def archive_brain_object(reel_id: str) -> tuple[bool, str]:
    """
    Move an active Brain Object from brains/<reel_id>.json to brains/archive/<reel_id>.json.
    Excludes it from all discovery commands, stats, and cache lookups.
    Safe: fails if active file does not exist, or if an archive collision already exists.
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided."

    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    archive_dir = Path(BRAINS_DIR) / "archive"
    archive_path = archive_dir / f"{reel_id}.json"

    if not active_path.is_file():
        return False, f"Reel '{reel_id}' not found in active library."

    if archive_path.is_file():
        return False, f"Archive collision: '{reel_id}' already exists in archive."

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        os.replace(active_path, archive_path)
        return True, f"Reel '{reel_id}' successfully archived."
    except Exception as e:
        return False, f"Failed to archive Reel '{reel_id}': {e}"


def restore_brain_object(reel_id: str) -> tuple[bool, str]:
    """
    Restore an archived Brain Object from brains/archive/<reel_id>.json back to brains/<reel_id>.json.
    Safe: fails if archive file does not exist, or if active library collision already exists.
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided."

    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    archive_path = Path(BRAINS_DIR) / "archive" / f"{reel_id}.json"

    if not archive_path.is_file():
        return False, f"Archived Reel '{reel_id}' not found in archive."

    if active_path.is_file():
        return False, f"Restore collision: Reel '{reel_id}' already exists in active library."

    try:
        Path(BRAINS_DIR).mkdir(parents=True, exist_ok=True)
        os.replace(archive_path, active_path)
        return True, f"Reel '{reel_id}' successfully restored to active library."
    except Exception as e:
        return False, f"Failed to restore Reel '{reel_id}': {e}"


def recategorize_brain_object(reel_id: str, new_category: str) -> tuple[bool, str, dict | None]:
    """
    Manually update category and normalize knowledge schema without invoking an LLM.
    Preserves summary, title, tags, and all valid existing knowledge fields.
    """
    if not reel_id or not isinstance(reel_id, str):
        return False, "Invalid Reel ID provided.", None

    if not new_category or not isinstance(new_category, str):
        return False, "Invalid category provided.", None

    # Normalize category name case-insensitively
    matched_category = None
    for cat in CATEGORIES:
        if cat.lower() == new_category.strip().lower():
            matched_category = cat
            break

    if not matched_category:
        valid_list = ", ".join(CATEGORIES)
        return False, f"Unknown category '{new_category}'. Valid categories are:\n{valid_list}", None

    active_path = Path(BRAINS_DIR) / f"{reel_id}.json"
    if not active_path.is_file():
        return False, f"Reel '{reel_id}' not found in active library.", None

    try:
        with open(active_path, "r", encoding="utf-8") as f:
            brain = json.load(f)
    except Exception as e:
        return False, f"Failed to read Brain Object '{reel_id}': {e}", None

    if "knowledge" not in brain or not isinstance(brain["knowledge"], dict):
        brain["knowledge"] = {"category": matched_category, "summary": "No summary available."}
    else:
        brain["knowledge"]["category"] = matched_category

    # Normalize schema against destination category
    try:
        from processing.knowledge_schema import normalize_knowledge_schema
        normalized = normalize_knowledge_schema(matched_category, brain["knowledge"])
        brain["knowledge"] = normalized
    except Exception as e:
        return False, f"Schema normalization failed for category '{matched_category}': {e}", None

    try:
        _atomic_write_json(active_path, brain)
        return True, f"Category updated to '{matched_category}'.", brain
    except Exception as e:
        return False, f"Failed to save updated Brain Object: {e}", None


def load_brain_object(reel_id: str) -> dict:
    """
    Load a specific Brain Object by its reel ID.
    """
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    if not brain_path.exists():
        raise FileNotFoundError(f"Brain Object not found: {brain_path}")

    with open(brain_path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_category(reel_id, category):
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    brain["knowledge"]["category"] = category
    _atomic_write_json(brain_path, brain)

    return brain


def update_latest_category(category):
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    return update_category(brain_path.stem, category)


def update_knowledge(reel_id: str, knowledge: dict) -> dict:
    """
    Update the knowledge section of a specific Brain Object by reel ID.
    """
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    if not brain_path.exists():
        raise FileNotFoundError(f"Brain Object not found: {brain_path}")

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    # Treat None or a completely empty dict {} as "no extraction was performed".
    # A dict that has keys — even if all values are empty strings or empty lists —
    # is a valid extractor result (e.g. an Other reel with no useful fields) and
    # must still be persisted so OneNote publishing is not silently skipped.
    if not knowledge:
        print("Extractor returned no output; keeping existing Brain Object knowledge.")
        return brain

    existing_category = brain["knowledge"].get("category")

    brain["knowledge"] = knowledge

    if existing_category and not brain["knowledge"].get("category"):
        brain["knowledge"]["category"] = existing_category

    _atomic_write_json(brain_path, brain)

    return brain


def update_latest_knowledge(knowledge: dict):
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    return update_knowledge(brain_path.stem, knowledge)


def update_latest_programming_knowledge(knowledge):
    return update_latest_knowledge(knowledge)


def update_vision_analysis(reel_id, vision_analysis: dict):
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    if "content" not in brain:
        brain["content"] = {}

    brain["content"]["vision_analysis"] = vision_analysis
    _atomic_write_json(brain_path, brain)

    return brain


def update_latest_vision_analysis(vision_analysis: dict):
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    return update_vision_analysis(brain_path.stem, vision_analysis)


def load_latest_brain_object():
    """
    Load the most recently created Brain Object.
    """
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    return load_brain_object(brain_path.stem)


def scan_valid_brain_objects() -> list[dict]:
    """
    Scan and load all fully valid Brain Objects from BRAINS_DIR.
    Safely ignores malformed, partial, or corrupted files.
    Read-only: does not modify any files or acquire ingestion locks.
    """
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
            # Skip unparseable or corrupted files safely
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

    # Direct string fields
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

    # Category-specific structured list fields
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

    # Sort newest first
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
