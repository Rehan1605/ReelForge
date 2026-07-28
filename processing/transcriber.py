import os
from pathlib import Path

from config import FFMPEG_PATH, WHISPER_MODEL, WORKSPACE_DIR

# Tell Python where FFmpeg is BEFORE importing Whisper
os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ["PATH"]

import whisper

# Load model only once
model = whisper.load_model(WHISPER_MODEL)


def transcribe_latest_reel():
    reels_folder = Path(WORKSPACE_DIR)

    mp4_file = max(
        reels_folder.glob("*.mp4"),
        key=lambda f: f.stat().st_mtime
    )

    print("Transcribing audio...")

    result = model.transcribe(str(mp4_file))

    transcript = result["text"].strip()

    print("Transcription Complete!")

    return transcript
