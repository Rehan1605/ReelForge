import json
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


def create_brain_object(source_url, metadata=None):
    if metadata is None:
        metadata_file = _latest_file("*.info.json")

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    mp4_file = _latest_file("*.mp4")
    thumbnail_file = _latest_thumbnail()
    shortcode = _shortcode_from_metadata(source_url, metadata)
    reel_id = metadata.get("id") or shortcode
    caption = metadata.get("description") or ""

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

    # Save
    Path(BRAINS_DIR).mkdir(exist_ok=True)

    output = Path(BRAINS_DIR) / f"{reel_id}.json"

    with open(output, "w", encoding="utf-8") as f:
        json.dump(reel, f, indent=4)

    print(f"Master JSON Created: {output}")

    return reel


def update_category(reel_id, category):
    brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    brain["knowledge"]["category"] = category

    with open(brain_path, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=4)

    return brain


def update_latest_category(category):
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    return update_category(brain_path.stem, category)


def update_latest_knowledge(knowledge: dict):
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)

    if not any(knowledge.values()):
        print("Extractor output was empty; keeping existing Brain Object knowledge.")
        return brain

    existing_category = brain["knowledge"].get("category")

    brain["knowledge"] = knowledge

    if existing_category and not brain["knowledge"].get("category"):
        brain["knowledge"]["category"] = existing_category

    with open(brain_path, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=4)

    return brain


def update_latest_programming_knowledge(knowledge):
    return update_latest_knowledge(knowledge)

def load_latest_brain_object():
    """
    Load the most recently created Brain Object.
    """
    brain_path = max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )

    with open(brain_path, "r", encoding="utf-8") as f:
        return json.load(f)
