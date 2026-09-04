import os
from pathlib import Path

from config import FFMPEG_PATH, WHISPER_MODEL, WORKSPACE_DIR

# Tell Python where FFmpeg is BEFORE importing Whisper
os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ["PATH"]

import whisper

# Load model only once
model = whisper.load_model(WHISPER_MODEL)


def transcribe_reel(video_path: Path | str) -> str:
    video_file = Path(video_path).resolve()

    if not video_file.is_file():
        raise FileNotFoundError(f"Video file not found: {video_file}")

    print(f"Transcribing audio from {video_file.name}...")

    result = model.transcribe(str(video_file))

    transcript = result["text"].strip()

    print("Transcription Complete!")

    return transcript


def transcribe_latest_reel():
    reels_folder = Path(WORKSPACE_DIR)

    mp4_file = max(
        reels_folder.glob("*.mp4"),
        key=lambda f: f.stat().st_mtime
    )

    return transcribe_reel(mp4_file)
