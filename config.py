import os
from pathlib import Path


def _load_env_file(file_path: str = ".env"):
    env_path = Path(file_path)

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


_load_env_file(".env")
_load_env_file("atlas-credentials.env")

MICROSOFT_CLIENT_ID = _required_env("MICROSOFT_CLIENT_ID")

# Microsoft tenant for the OAuth authority URL.
# 'common' -> any personal or work/school account (current behavior).
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "common").strip()

# Microsoft OAuth authority URL. Derived from MICROSOFT_TENANT_ID so the
# existing 'common' default stays compatible with personal + work/school
# accounts while remaining overridable via MICROSOFT_AUTHORITY.
MICROSOFT_AUTHORITY = os.getenv(
    "MICROSOFT_AUTHORITY",
    f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}",
).strip()

# Public redirect URI for the V3.3 OAuth callback server (PKCE / public client).
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/auth/callback").strip()
OAUTH_LISTEN_PORT = int(os.getenv("OAUTH_LISTEN_PORT", "8080"))
OAUTH_BASE_URL = os.getenv("OAUTH_BASE_URL", "http://localhost:8080").strip()
# Host/interface the local OAuth callback server binds to (default loopback).
OAUTH_HOST = os.getenv("OAUTH_HOST", "127.0.0.1").strip()

# Server-side secret used ONLY to sign/verify OAuth state tokens in Layer 2.
# Never hardcoded below: it must be provided via environment variables.
# An empty value disables state signing until Layer 2 explicitly validates it.
OAUTH_STATE_SECRET = os.getenv("OAUTH_STATE_SECRET", "").strip()

BOT_TOKEN = _required_env("TELEGRAM_BOT_TOKEN")

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "reelforge").strip()

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
