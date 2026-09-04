import os
from pathlib import Path


def _load_env_file():
    env_path = Path(".env")

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _required_env(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


_load_env_file()

MICROSOFT_CLIENT_ID = _required_env("MICROSOFT_CLIENT_ID")

BOT_TOKEN = _required_env("TELEGRAM_BOT_TOKEN")

FFMPEG_PATH = os.getenv(
    "FFMPEG_PATH",
    r"C:\Users\Rehan's Lenovo\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin",
)

TEXT_MODEL = os.getenv("TEXT_MODEL", "qwen2.5:7b-instruct")
EVALUATION_MODEL = os.getenv("EVALUATION_MODEL", TEXT_MODEL)
VISION_MODEL = os.getenv("VISION_MODEL", "llama3.2-vision:latest")
OMNIROUTE_BASE_URL = os.getenv("OMNIROUTE_BASE_URL", os.getenv("OPENAI_BASE_URL", "http://localhost:20128/v1"))
OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
VISION_MAX_FRAMES = int(os.getenv("VISION_MAX_FRAMES", "8"))

WORKSPACE_DIR = "reels"

BRAINS_DIR = "brains"

KEEP_VIDEOS = False

WHISPER_MODEL = "base"

OLLAMA_MODEL = "llama3.2-vision"

CATEGORIES = [
    "Programming",
    "AI",
    "Food",
    "Photography",
    "Gym",
    "Movies & Edits",
    "Travel",
    "Finance",
    "Productivity",
    "Other",
]
