# ── Stage 1: Builder ─────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# System deps for OpenCV, FFmpeg, and PyTorch
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.3
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VENV_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

WORKDIR /app

# Install dependencies
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only=main --no-root && rm -rf $POETRY_CACHE_DIR

# ── Stage 2: GPU Runtime ─────────────────────────────────────────────────
# Switch to nvidia/cuda base for GPU deployment (RunPod / Cloud Run with GPU)
# For CPU-only, keep python:3.11-slim and skip this FROM
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS gpu-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-distutils \
    python3-pip \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python

# ── Stage 3: Final ───────────────────────────────────────────────────────
# Switch base to gpu-runtime for GPU deployment, builder for CPU-only
FROM builder AS final

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY app/ ./app/

# Non-root user for security
RUN useradd --create-home --shell /bin/bash pitchlens && \
    chown -R pitchlens:pitchlens /app
USER pitchlens

# GPU PyTorch (override CPU wheels from poetry)
# Uncomment for GPU deployment:
# RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu121

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
