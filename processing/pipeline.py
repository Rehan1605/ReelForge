import traceback
from pathlib import Path

from config import BRAINS_DIR
from download.downloader import acquire_reel
from storage.brain_object import (
    load_latest_brain_object,
    update_latest_category,
    update_latest_knowledge,
)


def _notify(progress_callback, message):
    if progress_callback is not None:
        progress_callback(message)


def _latest_brain_path():
    return max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )


def _load_latest_brain_object():
    return load_latest_brain_object()


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
        vision_analysis = brain.get("content", {}).get("vision_analysis")

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
                "success": False,
                "onenote_success": False,
                "onenote_error": None,
                "brain": _load_latest_brain_object(),
                "brain_path": _latest_brain_path(),
                "transcript": transcript,
                "category": None,
                "knowledge": None,
                "error": f"Categorization Failed: {e}",
            }

        from processing.dispatcher import dispatch

        knowledge = None

        try:
            _notify(progress_callback, "Extracting Knowledge")
            print("Dispatching extractor...")
            knowledge = dispatch(category, caption, transcript, vision_analysis=vision_analysis)
            print("Extractor completed.")
            print("Updating Brain Object knowledge...")
            brain = update_latest_knowledge(knowledge)
            print("Knowledge updated.")
        except Exception as e:
            print(f"Knowledge Extraction Failed: {e}")
            return {
                "success": False,
                "onenote_success": False,
                "onenote_error": None,
                "brain": _load_latest_brain_object(),
                "brain_path": _latest_brain_path(),
                "transcript": transcript,
                "category": category,
                "knowledge": None,
                "error": f"Knowledge Extraction Failed: {e}",
            }

        brain = _load_latest_brain_object()
        category = brain.get("knowledge", {}).get("category")

        onenote_success = False
        onenote_error = None

        if not category:
            print("Warning: Brain Object category missing; skipping OneNote publishing.")
            onenote_error = "Brain Object category missing"
        else:
            try:
                from onenote.writer import OneNoteWriter

                _notify(progress_callback, "Publishing to OneNote")
                print("Publishing to OneNote...")
                writer = OneNoteWriter()
                writer.write(brain)
                onenote_success = True
                print("✅ OneNote page created successfully.")
            except Exception as e:
                onenote_success = False
                onenote_error = str(e)
                print(f"⚠️ OneNote publishing failed: {e}")

        brain_path = _latest_brain_path()

        if onenote_success:
            print("✅ Pipeline completed successfully (OneNote published).")
        else:
            print(f"⚠️ Pipeline completed extraction, but OneNote failed: {onenote_error}")

        return {
            "success": True,
            "onenote_success": onenote_success,
            "onenote_error": onenote_error,
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
            "onenote_success": False,
            "onenote_error": None,
            "brain": None,
            "brain_path": None,
            "transcript": None,
            "category": None,
            "knowledge": None,
            "error": str(e),
        }
