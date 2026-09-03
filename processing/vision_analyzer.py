import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import requests

from config import (
    BRAINS_DIR,
    FFMPEG_PATH,
    OMNIROUTE_API_KEY,
    OMNIROUTE_BASE_URL,
    VISION_MAX_FRAMES,
    VISION_MODEL,
    WORKSPACE_DIR,
)
from storage.brain_object import (
    load_latest_brain_object,
    update_latest_vision_analysis,
    update_vision_analysis,
)


def _get_binary_path(name: str) -> str:
    """
    Locate the requested executable (ffmpeg or ffprobe), checking FFMPEG_PATH first
    and falling back to system PATH.
    """
    ext = ".exe" if os.name == "nt" else ""
    binary_name = f"{name}{ext}"

    if FFMPEG_PATH:
        ffmpeg_dir = Path(FFMPEG_PATH)
        if ffmpeg_dir.is_dir():
            candidate = ffmpeg_dir / binary_name
            if candidate.is_file():
                return str(candidate)
        elif ffmpeg_dir.is_file() and ffmpeg_dir.name.lower() == binary_name.lower():
            if name.lower() == "ffmpeg":
                return str(ffmpeg_dir)
            sibling = ffmpeg_dir.parent / binary_name
            if sibling.is_file():
                return str(sibling)

    return binary_name


def _get_video_duration(video_path: Path) -> float | None:
    """
    Extract the video duration in seconds using ffprobe.
    Returns float seconds or None if duration cannot be extracted.
    """
    ffprobe_bin = _get_binary_path("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        output = result.stdout.strip()
        if output:
            duration = float(output)
            if duration > 0:
                return duration
    except Exception as e:
        print(f"Warning: Could not retrieve video duration with ffprobe: {e}")

    return None


def get_latest_reel_path() -> Path:
    """
    Find the most recently modified MP4 video in WORKSPACE_DIR.
    """
    reels_folder = Path(WORKSPACE_DIR)
    if not reels_folder.exists():
        raise FileNotFoundError(f"Reels directory not found: {WORKSPACE_DIR}")

    mp4_files = list(reels_folder.glob("*.mp4"))
    if not mp4_files:
        raise FileNotFoundError(f"No MP4 files found in {WORKSPACE_DIR}")

    return max(mp4_files, key=lambda f: f.stat().st_mtime)


def sample_frames(
    video_path: Path | str,
    max_frames: int | None = None,
    output_dir: Path | str | None = None,
) -> list[Path]:
    """
    Sample representative, evenly-spaced frames from a video using FFmpeg.
    Handles short videos, missing duration metadata, and creates JPEG files.

    Returns a list of Path objects for the sampled frame images.
    """
    video_file = Path(video_path).resolve()
    if not video_file.is_file():
        raise FileNotFoundError(f"Video file not found: {video_file}")

    if max_frames is None:
        max_frames = VISION_MAX_FRAMES

    max_frames = max(1, int(max_frames))

    if output_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="reelforge_frames_"))
    else:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = _get_binary_path("ffmpeg")
    duration = _get_video_duration(video_file)

    sampled_frames: list[Path] = []

    if duration and duration > 0.1:
        # Determine actual number of frames to sample conservatively
        if duration < 1.0:
            num_frames = 1
        elif duration < max_frames:
            num_frames = max(1, min(max_frames, int(duration * 2)))
        else:
            num_frames = max_frames

        # Calculate evenly spaced timestamps
        timestamps = [
            (i + 0.5) * (duration / num_frames) for i in range(num_frames)
        ]

        for idx, ts in enumerate(timestamps):
            frame_path = target_dir / f"frame_{idx:03d}.jpg"
            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                str(video_file),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ]

            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=20,
                )
                if frame_path.is_file() and frame_path.stat().st_size > 0:
                    sampled_frames.append(frame_path)
            except Exception as e:
                print(f"Warning: Failed to extract frame at timestamp {ts:.2f}s: {e}")

    # Fallback if timestamp seeking produced no frames or duration was unknown
    if not sampled_frames:
        print("Fallback frame extraction using fps filter...")
        pattern = target_dir / "fallback_frame_%03d.jpg"
        fallback_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_file),
            "-vf",
            f"fps=1/2",
            "-vframes",
            str(max_frames),
            "-q:v",
            "2",
            str(pattern),
        ]
        try:
            subprocess.run(
                fallback_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            sampled_frames = sorted(target_dir.glob("fallback_frame_*.jpg"))
        except Exception as e:
            raise RuntimeError(f"FFmpeg fallback frame extraction failed: {e}") from e

    if not sampled_frames:
        raise RuntimeError(f"No frames could be extracted from video: {video_file}")

    return sampled_frames


def _encode_image_to_base64(image_path: Path) -> str:
    """
    Read an image file and return an OpenAI-compatible data URL string.
    """
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _strip_fences(text: str) -> str:
    """
    Remove markdown code fences (e.g. ```json ... ```) or conversational fluff.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # If model returned text with JSON object inside { ... }
    brace_match = re.search(r"(\{[\s\S]*\})", text)
    if brace_match:
        return brace_match.group(1).strip()

    return text


def _build_prompt(caption: str = "", transcript: str = "") -> str:
    """
    Load the vision analyzer prompt template and inject caption & transcript.
    """
    prompt_path = Path("prompts") / "vision_analyzer.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template missing: {prompt_path}")

    template = prompt_path.read_text(encoding="utf-8")
    return (
        template.replace("{caption}", caption or "None")
        .replace("{transcript}", transcript or "None")
    )


def call_vision_model(
    prompt_text: str,
    frame_paths: list[Path],
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Call an OpenAI-compatible chat-completions endpoint with prompt and base64 image frames.
    """
    target_model = model or VISION_MODEL
    target_base_url = (base_url or OMNIROUTE_BASE_URL).rstrip("/")
    target_api_key = api_key if api_key is not None else OMNIROUTE_API_KEY

    endpoint = f"{target_base_url}/chat/completions"

    # Construct user content items
    user_content: list[dict] = [
        {"type": "text", "text": prompt_text}
    ]

    for frame_path in frame_paths:
        data_url = _encode_image_to_base64(frame_path)
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": data_url
            }
        })

    headers = {
        "Content-Type": "application/json",
    }
    if target_api_key:
        headers["Authorization"] = f"Bearer {target_api_key}"

    payload = {
        "model": target_model,
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "temperature": 0.1,
    }

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=120,
        )
    except requests.RequestException as e:
        raise ConnectionError(
            f"Failed to connect to vision gateway at {endpoint}: {e}"
        ) from e

    if response.status_code != 200:
        raise RuntimeError(
            f"Vision gateway returned HTTP {response.status_code}: {response.text}"
        )

    resp_text = response.text.strip()
    raw_message = ""

    if resp_text.startswith("data:"):
        # Accumulate SSE chunks returned by the gateway
        delta_contents = []
        for line in resp_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("data:"):
                chunk_payload = line_str[5:].strip()
                if chunk_payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(chunk_payload)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            delta_contents.append(content)
                except json.JSONDecodeError:
                    pass
        raw_message = "".join(delta_contents)
    else:
        try:
            data = response.json()
            raw_message = data["choices"][0]["message"]["content"]
        except Exception as e:
            raise ValueError(
                f"Vision gateway response is not valid JSON or unexpected structure: {response.text}"
            ) from e

    cleaned_json_text = _strip_fences(raw_message)

    try:
        parsed_result = json.loads(cleaned_json_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Vision model output could not be decoded as JSON.\nRaw output:\n{raw_message}"
        ) from e

    return _validate_and_normalize_schema(parsed_result)


def _validate_and_normalize_schema(data: dict) -> dict:
    """
    Ensure the resulting dictionary contains all expected keys with correct types.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Vision analysis output must be a dictionary, got {type(data)}")

    expected_schema = {
        "visual_summary": "",
        "on_screen_text": [],
        "visible_objects": [],
        "visible_entities": [],
        "visible_steps": [],
        "visible_code": [],
        "visible_settings": [],
        "visual_facts": [],
        "uncertainties": [],
    }

    normalized: dict = {}

    for key, default_val in expected_schema.items():
        val = data.get(key)
        if isinstance(default_val, list):
            if isinstance(val, list):
                normalized[key] = val
            elif val is None:
                normalized[key] = []
            elif isinstance(val, str):
                normalized[key] = [val] if val.strip() else []
            else:
                normalized[key] = list(val) if hasattr(val, "__iter__") else [str(val)]
        else:
            normalized[key] = str(val) if val is not None else default_val

    # Preserve any additional structured keys returned by model
    for k, v in data.items():
        if k not in normalized:
            normalized[k] = v

    return normalized


def format_vision_analysis(vision_analysis: dict | str | None) -> str:
    """
    Format a vision_analysis dictionary into a clean, human-readable prompt block.
    Returns "None" if vision_analysis is missing, None, or empty.
    """
    if not vision_analysis:
        return "None"

    if isinstance(vision_analysis, str):
        cleaned = vision_analysis.strip()
        return cleaned if cleaned else "None"

    if not isinstance(vision_analysis, dict):
        return "None"

    parts = []

    # 1. Visual Summary
    summary = vision_analysis.get("visual_summary")
    if summary and isinstance(summary, str) and summary.strip():
        parts.append(f"Visual Summary:\n{summary.strip()}")

    # 2. On-screen Text
    text_items = vision_analysis.get("on_screen_text") or []
    if text_items and isinstance(text_items, list):
        filtered = [str(item).strip() for item in text_items if str(item).strip()]
        if filtered:
            parts.append("On-Screen Text:\n" + "\n".join(f"- {item}" for item in filtered))

    # 3. Visible Steps
    steps = vision_analysis.get("visible_steps") or []
    if steps and isinstance(steps, list):
        filtered = [str(step).strip() for step in steps if str(step).strip()]
        if filtered:
            parts.append("Visible Steps:\n" + "\n".join(f"- {step}" for step in filtered))

    # 4. Visible Objects & Tools
    objects = vision_analysis.get("visible_objects") or []
    if objects and isinstance(objects, list):
        filtered = [str(obj).strip() for obj in objects if str(obj).strip()]
        if filtered:
            parts.append("Visible Objects / Equipment:\n" + "\n".join(f"- {obj}" for obj in filtered))

    # 5. Visible Code
    code_items = vision_analysis.get("visible_code") or []
    if code_items and isinstance(code_items, list):
        filtered = [str(c).strip() for c in code_items if str(c).strip()]
        if filtered:
            parts.append("Visible Code:\n" + "\n".join(f"- {c}" for c in filtered))

    # 6. Visible Settings / UI Configuration
    settings = vision_analysis.get("visible_settings") or []
    if settings and isinstance(settings, list):
        filtered = [str(s).strip() for s in settings if str(s).strip()]
        if filtered:
            parts.append("Visible Settings:\n" + "\n".join(f"- {s}" for s in filtered))

    # 7. Visible Entities
    entities = vision_analysis.get("visible_entities") or []
    if entities and isinstance(entities, list):
        filtered = [str(e).strip() for e in entities if str(e).strip()]
        if filtered:
            parts.append("Visible Entities:\n" + "\n".join(f"- {e}" for e in filtered))

    # 8. Visual Facts
    facts = vision_analysis.get("visual_facts") or []
    if facts and isinstance(facts, list):
        filtered = [str(f).strip() for f in facts if str(f).strip()]
        if filtered:
            parts.append("Visual Facts:\n" + "\n".join(f"- {f}" for f in filtered))

    # 9. Uncertainties
    uncertainties = vision_analysis.get("uncertainties") or []
    if uncertainties and isinstance(uncertainties, list):
        filtered = [str(u).strip() for u in uncertainties if str(u).strip()]
        if filtered:
            parts.append("Visual Uncertainties:\n" + "\n".join(f"- {u}" for u in filtered))

    if not parts:
        return "None"

    return "\n\n".join(parts)


def analyze_vision(
    video_path: Path | str,
    caption: str = "",
    transcript: str = "",
    update_brain: bool = False,
    reel_id: str | None = None,
    max_frames: int | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Sample frames from a video, query the vision model, parse the response,
    clean up temporary files, and optionally update the Brain Object.
    """
    video_file = Path(video_path).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="reelforge_vision_"))

    try:
        print(f"Sampling frames from {video_file.name}...")
        frames = sample_frames(video_file, max_frames=max_frames, output_dir=temp_dir)
        print(f"Extracted {len(frames)} representative frames.")

        prompt_text = _build_prompt(caption=caption, transcript=transcript)

        print(f"Calling vision model via gateway ({model or VISION_MODEL})...")
        vision_analysis = call_vision_model(
            prompt_text=prompt_text,
            frame_paths=frames,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        print("Vision analysis complete.")

        if update_brain:
            print("Updating Brain Object vision_analysis...")
            if reel_id:
                update_vision_analysis(reel_id, vision_analysis)
            else:
                update_latest_vision_analysis(vision_analysis)
            print("Brain Object updated.")

        return vision_analysis

    finally:
        # Guarantee cleanup of all temporary frame files
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def analyze_latest_reel(
    caption: str | None = None,
    transcript: str | None = None,
    update_brain: bool = True,
    max_frames: int | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Find the latest downloaded Reel and Brain Object, run visual analysis,
    and update content.vision_analysis in the Brain Object.
    """
    latest_video = get_latest_reel_path()
    print(f"Located latest Reel video: {latest_video}")

    try:
        brain = load_latest_brain_object()
        brain_caption = brain.get("content", {}).get("caption", "") or ""
        brain_transcript = brain.get("content", {}).get("transcript", "") or ""
        brain_id = brain.get("id")
    except Exception as e:
        print(f"Notice: Could not load latest Brain Object ({e}). Proceeding without it.")
        brain_caption = ""
        brain_transcript = ""
        brain_id = None

    effective_caption = caption if caption is not None else brain_caption
    effective_transcript = transcript if transcript is not None else brain_transcript

    return analyze_vision(
        video_path=latest_video,
        caption=effective_caption,
        transcript=effective_transcript,
        update_brain=update_brain,
        reel_id=brain_id,
        max_frames=max_frames,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )


def test_sampling(
    video_path: Path | str | None = None,
    max_frames: int | None = None,
) -> list[dict]:
    """
    Test frame sampling independently without making any API calls to a vision model.
    Prints sampling metrics and cleans up extracted files.
    """
    target_video = Path(video_path).resolve() if video_path else get_latest_reel_path()
    temp_dir = Path(tempfile.mkdtemp(prefix="reelforge_sample_test_"))

    try:
        print(f"Testing frame sampling for: {target_video}")
        duration = _get_video_duration(target_video)
        print(f"Video Duration: {duration:.2f}s" if duration else "Video Duration: Unknown")

        frames = sample_frames(target_video, max_frames=max_frames, output_dir=temp_dir)
        print(f"Successfully sampled {len(frames)} frames:")

        frame_info = []
        for idx, frame in enumerate(frames):
            size_kb = frame.stat().st_size / 1024
            info = {
                "index": idx + 1,
                "filename": frame.name,
                "size_kb": round(size_kb, 2),
            }
            frame_info.append(info)
            print(f"  Frame {idx + 1:02d}: {frame.name} ({size_kb:.1f} KB)")

        return frame_info

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            print("Temporary sampling test directory cleaned up.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ReelForge V2.1 Visual Analyzer CLI"
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Path to an MP4 video (defaults to latest reel in WORKSPACE_DIR)",
    )
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="Test frame sampling only without calling the vision model",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help=f"Maximum frames to sample (default: {VISION_MAX_FRAMES})",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"Vision model name (default: {VISION_MODEL})",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help=f"OpenAI-compatible base URL (default: {OMNIROUTE_BASE_URL})",
    )
    parser.add_argument(
        "--no-update-brain",
        action="store_true",
        help="Do not update the Brain Object with results",
    )

    args = parser.parse_args()

    try:
        if args.sample_only:
            test_sampling(video_path=args.video, max_frames=args.max_frames)
            sys.exit(0)

        result = analyze_latest_reel(
            update_brain=not args.no_update_brain,
            max_frames=args.max_frames,
            model=args.model,
            base_url=args.base_url,
        )

        print("\n=== VISUAL ANALYSIS RESULT ===")
        print(json.dumps(result, indent=2))
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Visual Analysis Failed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
