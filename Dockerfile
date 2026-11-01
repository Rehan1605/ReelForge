# syntax=docker/dockerfile:1

# Update this immutable upstream commit deliberately; see WORKER_OPERATIONS.md.
ARG WHISPER_CPP_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037

# Build for the target architecture so buildx emits matching amd64/arm64
# binaries. Cross-architecture builds require native builders or QEMU support.
FROM --platform=$TARGETPLATFORM python:3.12-slim AS whisper-build
ARG WHISPER_CPP_COMMIT
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libgomp1 \
        libgomp-12-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git init /src \
    && git -C /src remote add origin https://github.com/ggml-org/whisper.cpp.git \
    && git -C /src fetch --depth 1 origin refs/tags/v1.9.5 \
    && git -C /src checkout --detach "${WHISPER_CPP_COMMIT}" \
    && test "$(git -C /src rev-parse HEAD)" = "${WHISPER_CPP_COMMIT}"
RUN cmake -S /src -B /src/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_IS_DEV=OFF \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
        -DWHISPER_BUILD_SERVER=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_OPENMP=ON \
        -DGGML_CUDA=OFF \
        -DGGML_METAL=OFF \
    && cmake --build /src/build --config Release --parallel 2 --target whisper-cli \
    && install -D /src/build/bin/whisper-cli /out/usr/local/bin/whisper-cli \
    && ldd /out/usr/local/bin/whisper-cli | tee /tmp/whisper-cli-ldd.txt \
    && ! grep -q "not found" /tmp/whisper-cli-ldd.txt

# Download once during image build, never on container startup. Upstream's
# model table publishes SHA-1 for the exact multilingual Base artifact.
FROM --platform=$BUILDPLATFORM python:3.12-slim AS whisper-model
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/reelforge/models \
    && curl --fail --location --retry 3 --retry-all-errors \
        https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin \
        --output /opt/reelforge/models/ggml-base.bin \
    && echo "465707469ff3a37a2b9b8d8f89f2f99de7299dac  /opt/reelforge/models/ggml-base.bin" | sha1sum --check -

# Runtime contains Python application dependencies, FFmpeg, and the OpenMP
# runtime used by the CPU whisper.cpp build; no compiler, git, or PyTorch.
FROM python:3.12-slim AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WHISPER_CPP_BIN=whisper-cli \
    WHISPER_CPP_THREADS=2 \
    WHISPER_CPP_TIMEOUT=300

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=whisper-build /out/usr/local/bin/whisper-cli /usr/local/bin/whisper-cli

COPY requirements.txt .
RUN pip install -r requirements.txt

# Source code only. Secrets and local artifacts are excluded via .dockerignore
# and must be supplied as environment variables at runtime.
COPY . .

# Primary entry point: Telegram bot. Default config starts the embedded worker;
# setting REELFORGE_EMBEDDED_WORKER=0 makes this bot-only.
CMD ["python", "bot/telegram_bot.py"]

# Default image includes the Base model. To avoid embedding it in the image,
# build --target runtime-base and mount a persistent model directory instead.
FROM runtime-base AS runtime
ENV WHISPER_CPP_MODEL_PATH=/opt/reelforge/models/ggml-base.bin
COPY --from=whisper-model /opt/reelforge/models/ggml-base.bin /opt/reelforge/models/ggml-base.bin
RUN whisper-cli --help >/dev/null 2>&1 \
    && test -x "$(command -v whisper-cli)" \
    && test -r /opt/reelforge/models/ggml-base.bin \
    && ffmpeg -version >/dev/null 2>&1
