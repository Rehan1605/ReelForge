"""Local audio transcription through the whisper.cpp command-line client."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import (
    FFMPEG_PATH,
    WHISPER_CPP_BIN,
    WHISPER_CPP_MODEL_PATH,
    WHISPER_CPP_THREADS,
    WHISPER_CPP_TIMEOUT,
)
from processing.worker_workspace import effective_workspace


# Keep FFmpeg discoverable for this process when an operator supplies a custom
# binary directory. No machine-specific path is selected by the transcriber.
if FFMPEG_PATH and FFMPEG_PATH not in os.environ["PATH"]:
    os.environ["PATH"] = FFMPEG_PATH + os.pathsep + os.environ["PATH"]


def _resolve_executable(binary: str, label: str) -> str:
    resolved = shutil.which(binary)
    if not resolved:
        raise FileNotFoundError(f"{label} executable is unavailable")
    return resolved


def validate_transcription_runtime() -> bool:
    """Validate local transcription tools without starting an inference job."""
    if not WHISPER_CPP_MODEL_PATH:
        raise RuntimeError("WHISPER_CPP_MODEL_PATH must point to ggml-base.bin")
    model_path = Path(WHISPER_CPP_MODEL_PATH).expanduser().resolve()
    if model_path.name.lower() != "ggml-base.bin":
        raise ValueError("WHISPER_CPP_MODEL_PATH must name the multilingual ggml-base.bin model")
    if not model_path.is_file() or not os.access(model_path, os.R_OK):
        raise FileNotFoundError("Configured multilingual whisper.cpp Base model is missing or unreadable")

    for binary, label in (("ffmpeg", "FFmpeg"), (WHISPER_CPP_BIN, "whisper.cpp")):
        # shutil.which only returns candidates that the current platform can
        # execute (including PATHEXT handling on Windows).
        _resolve_executable(binary, label)

    if WHISPER_CPP_THREADS < 1:
        raise ValueError("WHISPER_CPP_THREADS must be a positive integer")
    if WHISPER_CPP_TIMEOUT < 1:
        raise ValueError("WHISPER_CPP_TIMEOUT must be a positive integer")
    return True


def _run_process(args: list[str], timeout: int, label: str) -> None:
    """Run a child without a shell and keep third-party output out of logs."""
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Could not start {label}") from exc

    try:
        _, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise TimeoutError(f"{label} exceeded the configured timeout") from exc

    if process.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {process.returncode}")


def transcribe_reel(video_path: Path | str) -> str:
    """Return a trimmed transcript for a video using configured whisper.cpp."""
    video_file = Path(video_path).expanduser().resolve()
    if not video_file.is_file():
        raise FileNotFoundError("Video file not found")
    validate_transcription_runtime()
    model_path = Path(WHISPER_CPP_MODEL_PATH).expanduser().resolve()
    ffmpeg = _resolve_executable("ffmpeg", "FFmpeg")
    whisper_cli = _resolve_executable(WHISPER_CPP_BIN, "whisper.cpp")

    # TemporaryDirectory is unique for each call and removes WAV/output files
    # regardless of conversion, transcription, parsing, or timeout failures.
    with tempfile.TemporaryDirectory(prefix="reelforge_transcription_") as temp_dir:
        temp_path = Path(temp_dir)
        wav_path = temp_path / "audio.wav"
        output_prefix = temp_path / "transcript"

        _run_process(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video_file),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(wav_path),
            ],
            WHISPER_CPP_TIMEOUT,
            "FFmpeg audio conversion",
        )
        if not wav_path.is_file():
            raise RuntimeError("FFmpeg completed without producing a WAV file")

        _run_process(
            [
                whisper_cli,
                "-m",
                str(model_path),
                "-f",
                str(wav_path),
                "-t",
                str(WHISPER_CPP_THREADS),
                "-otxt",
                "-of",
                str(output_prefix),
                "-nt",
                "-np",
            ],
            WHISPER_CPP_TIMEOUT,
            "whisper.cpp transcription",
        )

        # whisper.cpp's documented -otxt/-of interface writes this file;
        # stdout is intentionally not treated as the transcript.
        transcript_path = output_prefix.with_suffix(".txt")
        if not transcript_path.is_file():
            raise RuntimeError("whisper.cpp completed without producing transcript output")
        return transcript_path.read_text(encoding="utf-8").strip()


def transcribe_latest_reel():
    reels_folder = effective_workspace()
    mp4_file = max(reels_folder.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
    return transcribe_reel(mp4_file)
