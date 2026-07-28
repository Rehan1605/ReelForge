import sys
import tempfile
from pathlib import Path

from yt_dlp import YoutubeDL


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("Instagram Reel URL: ")

    with tempfile.TemporaryDirectory(prefix="instabrain_ytdlp_") as temp_dir:
        output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")

        options = {
            "outtmpl": output_template,
            "quiet": False,
            "noplaylist": True,
        }

        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)

        print("\nMetadata keys:")
        for key in sorted(info.keys()):
            print(key)

        print("\nSelected metadata:")
        print(f"title: {info.get('title')}")
        print(f"description: {info.get('description')}")
        print(f"uploader: {info.get('uploader')}")
        print(f"upload_date: {info.get('upload_date')}")
        print(f"duration: {info.get('duration')}")
        print(f"thumbnail: {info.get('thumbnail')}")
        print(f"filepath: {filepath}")


if __name__ == "__main__":
    main()
