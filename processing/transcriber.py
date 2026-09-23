import os
import threading
from pathlib import Path

from config import FFMPEG_PATH, WHISPER_CACHE_DIR, WHISPER_MODEL
from processing.worker_workspace import effective_workspace

# Tell Python where FFmpeg is BEFORE Whisper uses it (applies to transcription).
# Only prepend when a custom directory is configured; otherwise rely on system PATH.
if FFMPEG_PATH and FFMPEG_PATH not in os.environ["PATH"]:
    os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ["PATH"]

_model = None
_model_lock = threading.Lock()


def _load_whisper_model():
    import whisper

    return whisper.load_model(WHISPER_MODEL, download_root=WHISPER_CACHE_DIR)


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_whisper_model()
    return _model


def reset_model_for_tests():
    global _model
    _model = None


def transcribe_reel(video_path: Path | str) -> str:
    video_file = Path(video_path).resolve()

    if not video_file.is_file():
        raise FileNotFoundError(f"Video file not found: {video_file}")

    print(f"Transcribing audio from {video_file.name}...")

    result = _get_model().transcribe(str(video_file))

    transcript = result["text"].strip()

    print("Transcription Complete!")

    return transcript


def transcribe_latest_reel():
    reels_folder = effective_workspace()

    mp4_file = max(
        reels_folder.glob("*.mp4"),
        key=lambda f: f.stat().st_mtime
    )

    return transcribe_reel(mp4_file)
