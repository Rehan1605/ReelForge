import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from config import BRAINS_DIR, WORKSPACE_DIR


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
    parts = [p for p in webpage_url.strip("/").split("/") if p]

    if parts:
        return parts[-1]

    return metadata.get("id")


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
        }
    }

    # Save atomically
    Path(BRAINS_DIR).mkdir(exist_ok=True)
    output = Path(BRAINS_DIR) / f"{reel_id}.json"
    _atomic_write_json(output, reel)

    print(f"Master JSON Created: {output}")

    return reel


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
