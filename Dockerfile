# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System runtime dependencies.
# ffmpeg: required by yt-dlp (bestvideo+bestaudio merge), Whisper audio
# decoding, and the vision analyzer (ffmpeg/ffprobe frame extraction).
# libgomp1: OpenMP runtime needed by CPU torch wheels on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch first so openai-whisper resolves against it instead of
# pulling the multi-GB CUDA wheels from PyPI.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY requirements.txt .
RUN pip install -r requirements.txt

# Source code only. Secrets and local artifacts are excluded via .dockerignore
# and must be supplied as environment variables at runtime.
COPY . .

# Primary entry point: ReelForge Telegram bot (polling, outbound only).
CMD ["python", "bot/telegram_bot.py"]