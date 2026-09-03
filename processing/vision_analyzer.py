import base64
import json
import re
import subprocess
from pathlib import Path

import requests

from config import FFMPEG_PATH, OMNIROUTE_BASE_URL, VISION_MODEL, VISION_MAX_FRAMES


PROMPT_PATH = Path("prompts") / "vision_analyzer.txt"


def _strip_fences(text):
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    return match.group(1).strip() if match else text


def _latest_video():
    videos = list(Path("reels").glob("*.mp4"))
    if not videos:
        raise FileNotFoundError("No downloaded Reel video found in the workspace.")
    return max(videos, key=lambda f: f.stat().st_mtime)


def _sample_frames(video_path, output_dir, max_frames):
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_frame in output_dir.glob("frame_*.jpg"):
        old_frame.unlink()

    ffmpeg = str(Path(FFMPEG_PATH) / "ffmpeg.exe") if FFMPEG_PATH else "ffmpeg"
    output_pattern = str(output_dir / "frame_%03d.jpg")

    # Select a small, evenly distributed set of frames. FFmpeg's fps filter
    # uses the video duration to determine a uniform sampling rate.
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={max_frames}/duration",
        "-frames:v",
        str(max_frames),
        "-q:v",
        "3",
        output_pattern,
    ]

    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "FFmpeg executable was not found. Check FFMPEG_PATH or system PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("FFmpeg failed while sampling Reel frames.") from exc

    frames = sorted(output_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("FFmpeg produced no frames from the Reel video.")

    return frames


def _image_data_url(frame_path):
    encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def analyze_latest_reel(caption="", transcript=""):
    video_path = _latest_video()
    frame_dir = Path("reels") / "vision_frames"
    frames = _sample_frames(video_path, frame_dir, VISION_MAX_FRAMES)

    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt.replace("{caption}", caption or "")
        .replace("{transcript}", transcript or "")
    )

    content = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": _image_data_url(frame)},
        }
        for frame in frames
    )

    endpoint = OMNIROUTE_BASE_URL.rstrip("/") + "/chat/completions"

    response = requests.post(
        endpoint,
        json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        },
        timeout=300,
    )
    response.raise_for_status()

    payload = response.json()
    try:
        result_text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Vision gateway returned an unexpected response.") from exc

    result = _strip_fences(result_text)

    try:
        analysis = json.loads(result)
    except json.JSONDecodeError as exc:
        raise ValueError("Vision model returned invalid JSON.") from exc

    if not isinstance(analysis, dict):
        raise ValueError("Vision model must return a JSON object.")

    analysis["frames_analyzed"] = len(frames)
    analysis["model"] = VISION_MODEL

    return analysis
