import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


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


_load_env_file(ROOT_DIR / ".env")
_load_env_file(ROOT_DIR / "atlas-credentials.env")

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

# FFmpeg binary directory. On Windows/this laptop, defaults to the installed
# WinGet build. On Linux/cloud workers, defaults to "" so ffmpeg/ffprobe are
# resolved from the system PATH (e.g. apt-installed ffmpeg in the container).
def _default_ffmpeg_path() -> str:
    if os.name == "nt":
        return r"C:\Users\Rehan's Lenovo\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"
    return ""


FFMPEG_PATH = os.getenv("FFMPEG_PATH", _default_ffmpeg_path()).strip()

TEXT_MODEL = os.getenv("TEXT_MODEL", "qwen2.5:7b-instruct")
EVALUATION_MODEL = os.getenv("EVALUATION_MODEL", TEXT_MODEL)
VISION_MODEL = os.getenv("VISION_MODEL", "llama3.2-vision:latest")
# AI gateway routing: explicit OMNIROUTE_BASE_URL wins, then OPENAI_BASE_URL
# fallback, then the current local OmniRoute default. This lets the gateway be
# hosted anywhere (env config only) while keeping local development unchanged.
OMNIROUTE_BASE_URL = (os.getenv("OMNIROUTE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "http://localhost:20128/v1")
# Remote gateways may require an API key; keys come only from the environment.
OMNIROUTE_API_KEY = (os.getenv("OMNIROUTE_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
VISION_MAX_FRAMES = int(os.getenv("VISION_MAX_FRAMES", "8"))

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", str(ROOT_DIR / "reels")).strip()

BRAINS_DIR = os.getenv("BRAINS_DIR", str(ROOT_DIR / "brains")).strip()

PROMPTS_DIR = os.getenv("PROMPTS_DIR", str(ROOT_DIR / "prompts")).strip()

KEEP_VIDEOS = False

# V3.5 Layer 2 — embedded worker toggle.
# Default ON for single-process local development.
# Set to 0 / false / no to disable the in-process worker inside run_bot(),
# so you can run ``python -m processing.worker`` as a separate process instead.
REELFORGE_EMBEDDED_WORKER = os.getenv("REELFORGE_EMBEDDED_WORKER", "1").strip().lower() not in (
    "0", "false", "no",
)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip()
WHISPER_CACHE_DIR = os.getenv("WHISPER_CACHE_DIR", "").strip() or None

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
