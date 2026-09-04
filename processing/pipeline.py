import traceback
from pathlib import Path

from config import BRAINS_DIR, KEEP_VIDEOS, WORKSPACE_DIR
from download.downloader import acquire_reel
from storage.brain_object import (
    extract_reel_id_from_url,
    get_cached_brain_object,
    load_brain_object,
    load_latest_brain_object,
    update_category,
    update_knowledge,
)


def _notify(progress_callback, message):
    if progress_callback is not None:
        progress_callback(message)


def _cleanup_reel_media(reel_id: str | None):
    """
    Remove local media files belonging to this specific reel if KEEP_VIDEOS is False.
    Never deletes Brain Objects or files belonging to other reels.
    """
    if KEEP_VIDEOS or not reel_id:
        return

    reels_dir = Path(WORKSPACE_DIR)
    if not reels_dir.exists():
        return

    for pattern in (f"{reel_id}.*", f"{reel_id}*.*"):
        for file in reels_dir.glob(pattern):
            if file.is_file():
                try:
                    file.unlink()
                    print(f"Cleaned up reel media file: {file.name}")
                except Exception as e:
                    print(f"Notice: Could not unlink {file.name}: {e}")


def _latest_brain_path():
    return max(
        Path(BRAINS_DIR).glob("*.json"),
        key=lambda f: f.stat().st_mtime
    )


def _load_latest_brain_object():
    return load_latest_brain_object()


def process_reel(url, progress_callback=None):
    # 1. Check for valid existing Brain Object cache before downloading
    candidate_id = extract_reel_id_from_url(url)
    if candidate_id:
        cached_brain = get_cached_brain_object(candidate_id)
        if cached_brain is not None:
            print(f"[OK] Reel '{candidate_id}' is already processed. Returning cached Brain Object.")
            _notify(progress_callback, "Already Processed (Using Cached Knowledge)")
            category = cached_brain.get("knowledge", {}).get("category")
            knowledge = cached_brain.get("knowledge")
            transcript = cached_brain.get("content", {}).get("transcript")
            brain_path = Path(BRAINS_DIR) / f"{candidate_id}.json"

            return {
                "success": True,
                "cached": True,
                "onenote_success": True,
                "onenote_error": None,
                "brain": cached_brain,
                "brain_path": brain_path,
                "transcript": transcript,
                "category": category,
                "knowledge": knowledge,
                "error": None,
            }

    # 2. Normal execution for new or incomplete reels
    reel_id = None
    try:
        _notify(progress_callback, "Downloading")
        brain = acquire_reel(url)
        reel_id = brain.get("id")
        video_path = Path(brain.get("media", {}).get("video_path", ""))
        caption = brain.get("content", {}).get("caption", "") or ""

        from processing.transcriber import transcribe_reel

        try:
            _notify(progress_callback, "Transcribing")
            print(f"Starting transcription for reel {reel_id} ({video_path.name})...")
            transcript = transcribe_reel(video_path) or ""
            print("Transcription complete.")
        except Exception as e:
            print(f"Transcription Failed: {e}")
            transcript = ""

        from processing.vision_analyzer import analyze_vision

        vision_analysis = None
        try:
            _notify(progress_callback, "Analyzing Video Frames")
            print(f"Starting vision analysis for reel {reel_id}...")
            vision_analysis = analyze_vision(
                video_path=video_path,
                caption=caption,
                transcript=transcript,
                update_brain=True,
                reel_id=reel_id,
            )
            print("Vision analysis complete.")
        except Exception as e:
            print(f"Vision Analysis Failed (proceeding without vision): {e}")
            if reel_id:
                try:
                    current_brain = load_brain_object(reel_id)
                    vision_analysis = current_brain.get("content", {}).get("vision_analysis")
                except Exception:
                    vision_analysis = None
            else:
                vision_analysis = None

        from processing.categorizer import categorize

        try:
            _notify(progress_callback, "Categorizing")
            print("Categorizing...")
            category = categorize(caption, transcript)
            print(f"Category: {category}")
            print(f"Updating category for reel {reel_id}...")
            brain = update_category(reel_id, category)
            print("Category updated.")
        except Exception as e:
            print(f"Categorization Failed: {e}")
            brain_path = (Path(BRAINS_DIR) / f"{reel_id}.json") if reel_id else None
            return {
                "success": False,
                "onenote_success": False,
                "onenote_error": None,
                "brain": load_brain_object(reel_id) if (brain_path and brain_path.exists()) else None,
                "brain_path": brain_path if (brain_path and brain_path.exists()) else None,
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
            print(f"Updating Brain Object knowledge for reel {reel_id}...")
            brain = update_knowledge(reel_id, knowledge)
            print("Knowledge updated.")
        except Exception as e:
            print(f"Knowledge Extraction Failed: {e}")
            brain_path = (Path(BRAINS_DIR) / f"{reel_id}.json") if reel_id else None
            return {
                "success": False,
                "onenote_success": False,
                "onenote_error": None,
                "brain": load_brain_object(reel_id) if (brain_path and brain_path.exists()) else None,
                "brain_path": brain_path if (brain_path and brain_path.exists()) else None,
                "transcript": transcript,
                "category": category,
                "knowledge": None,
                "error": f"Knowledge Extraction Failed: {e}",
            }

        brain = load_brain_object(reel_id)
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
                print("[OK] OneNote page created successfully.")
            except Exception as e:
                onenote_success = False
                onenote_error = str(e)
                print(f"[WARN] OneNote publishing failed: {e}")

        brain_path = Path(BRAINS_DIR) / f"{reel_id}.json"

        if onenote_success:
            print("[OK] Pipeline completed successfully (OneNote published).")
        else:
            print(f"[WARN] Pipeline completed extraction, but OneNote failed: {onenote_error}")

        return {
            "success": True,
            "cached": False,
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
        brain_path = (Path(BRAINS_DIR) / f"{reel_id}.json") if reel_id else None
        return {
            "success": False,
            "cached": False,
            "onenote_success": False,
            "onenote_error": None,
            "brain": load_brain_object(reel_id) if (brain_path and brain_path.exists()) else None,
            "brain_path": brain_path if (brain_path and brain_path.exists()) else None,
            "transcript": None,
            "category": None,
            "knowledge": None,
            "error": str(e),
        }
    finally:
        _cleanup_reel_media(reel_id)
