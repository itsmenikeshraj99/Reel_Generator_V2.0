# syntax=docker/dockerfile:1.6
# =============================================================
# Backend Dockerfile — Cloud Run
# Multi-stage: build deps in a builder, copy the venv into a slim
# runtime image. ~150MB final image.
# =============================================================

# ---------- builder ----------
FROM python:3.11-slim AS builder

# Build deps for any wheels that need compiling (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so this layer caches when only app code changes
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------- runtime ----------
FROM python:3.11-slim AS runtime

# Run as a non-root user for defense in depth. UID 65532 is the
# standard "nonroot" user in distroless images; we mirror it here.
RUN groupadd -g 65532 app && useradd -u 65532 -g app -m -s /usr/sbin/nologin app

# Copy the venv from the builder
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Application code
COPY --chown=app:app app ./app

USER 65532

# Cloud Run sets $PORT (default 8080). uvicorn reads it via shell.
ENV PORT=8080
EXPOSE 8080

# Use shell form so $PORT is expanded at runtime
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
