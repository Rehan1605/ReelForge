import traceback
from pathlib import Path

from yt_dlp import YoutubeDL

from processing.worker_workspace import effective_workspace
from storage.brain_object import create_brain_object


def clear_workspace():
    reels_folder = effective_workspace()

    if not reels_folder.exists():
        return

    for file in reels_folder.iterdir():
        if file.is_file():
            file.unlink()

    print("Workspace cleaned.")


def _download_with_ytdlp(url):
    out_dir = effective_workspace()
    out_dir.mkdir(parents=True, exist_ok=True)

    options = {
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "writethumbnail": True,
        "writeinfojson": True,
    }

    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=True)


def acquire_reel(url, user_id: str | None = None):
    print(f"\nDownloading: {url}")

    metadata = _download_with_ytdlp(url)

    print("Download Complete!")
    return create_brain_object(url, metadata, user_id=user_id)


def download_reel(url):
    try:
        from processing.pipeline import process_reel

        return process_reel(url)["success"]
    except Exception as e:
        traceback.print_exc()
        print(f"Download Failed: {e}")
        return False
