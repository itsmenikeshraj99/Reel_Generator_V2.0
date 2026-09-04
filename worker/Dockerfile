# syntax=docker/dockerfile:1.6
# =============================================================
# Worker Dockerfile — Railway / any Docker host
# Multi-stage with ffmpeg + ffprobe in the runtime image. ~1.2GB.
#
# Why apt-get ffmpeg instead of static-linked:
#   * Matches the version pinned by the local Windows install
#   * The image is bigger (~50MB more) but a known-good build
#   * Saves us from maintaining a separate download step
# =============================================================

# ---------- builder ----------
FROM python:3.11-slim AS builder

# Build deps for compiling C extensions: opencv, cryptography, scenedetect deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libffi-dev \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim AS runtime

# ffmpeg + ffprobe (the heavy hitters). libsm6/libxext/libxrender are
# OpenCV headless backend deps (cv2 needs them on Linux even if no display).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsm6 \
        libxext6 \
        libxrender1 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version | head -1 \
    && ffprobe -version | head -1

# Non-root user
RUN groupadd -g 65532 app && useradd -u 65532 -g app -m -s /usr/sbin/nologin app

# Copy venv + app code
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app worker ./worker
COPY --chown=app:app assets ./assets

# tini handles PID 1 signal forwarding (SIGTERM → uvicorn graceful shutdown)
# — Railway's lifecycle depends on this.
ENTRYPOINT ["/usr/bin/tini", "--"]

USER 65532

# Railway sends SIGTERM and waits up to ~10s before SIGKILL. The worker
# pipeline can take 10 min so we tell uvicorn to be patient; Railway's
# `healthcheckTimeout`/`restartPolicyType` is the real deadline.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec /opt/venv/bin/python -m worker.worker.main"]
