import json
import traceback
from pathlib import Path

from config import BRAINS_DIR
from download.downloader import acquire_reel
from storage.brain_object import update_latest_category, update_latest_knowledge


def _notify(progress_callback, message):
    if progress_callback is not None:
        progress_callback(message)


def _latest_brain_path():
    return max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )


def _load_latest_brain_object():
    brain_path = _latest_brain_path()

    with open(brain_path, "r", encoding="utf-8") as f:
        return json.load(f)


def process_reel(url, progress_callback=None):
    try:
        _notify(progress_callback, "Downloading")
        acquire_reel(url)

        from processing.transcriber import transcribe_latest_reel

        try:
            _notify(progress_callback, "Transcribing")
            print("Starting transcription...")
            transcript = transcribe_latest_reel() or ""
            print("Transcription complete.")
        except Exception as e:
            print(f"Transcription Failed: {e}")
            transcript = ""

        print("Loading latest Brain Object...")
        brain = _load_latest_brain_object()
        print("Brain Object loaded.")
        caption = brain.get("content", {}).get("caption", "") or ""

        from processing.categorizer import categorize

        try:
            _notify(progress_callback, "Categorizing")
            print("Categorizing...")
            category = categorize(caption, transcript)
            print(f"Category: {category}")
            print("Updating category...")
            brain = update_latest_category(category)
            print("Category updated.")
        except Exception as e:
            print(f"Categorization Failed: {e}")
            return {
                "success": True,
                "brain": _load_latest_brain_object(),
                "brain_path": _latest_brain_path(),
                "transcript": transcript,
                "category": None,
                "knowledge": None,
                "error": str(e),
            }

        from processing.dispatcher import dispatch

        knowledge = None

        try:
            _notify(progress_callback, "Extracting Knowledge")
            print("Dispatching extractor...")
            knowledge = dispatch(category, caption, transcript)
            print("Extractor completed.")
            print(f"Extractor output: {json.dumps(knowledge, indent=4)}")
            print("Updating Brain Object knowledge...")
            brain = update_latest_knowledge(knowledge)
            print("Knowledge updated.")
        except Exception as e:
            print(f"Knowledge Extraction Failed: {e}")
            brain = _load_latest_brain_object()

        brain_path = _latest_brain_path()

        print("Pipeline completed successfully.")

        return {
            "success": True,
            "brain": brain,
            "brain_path": brain_path,
            "transcript": transcript,
            "category": category,
            "knowledge": knowledge,
            "error": None,
        }

    except Exception as e:
        traceback.print_exc()
        print(f"Pipeline Failed: {e}")
        return {
            "success": False,
            "brain": None,
            "brain_path": None,
            "transcript": None,
            "category": None,
            "knowledge": None,
            "error": str(e),
        }
