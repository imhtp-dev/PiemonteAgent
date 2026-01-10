# syntax=docker/dockerfile:1.4
# OPTIMIZED: BuildKit cache enables fast rebuilds (5-15 min after first build)
# First build: will take some time (downloads everything)
# Subsequent builds: 5-15 minutes (uses cached packages)
# Azure deployment: 30 SECONDS (pulls ready image, NO building!)

# Multi-stage build with BuildKit cache optimization
FROM python:3.11-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    NLTK_DATA=/usr/local/nltk_data \
    TORCH_HOME=/opt/torch \
    PIP_NO_CACHE_DIR=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies (rarely changes)
FROM base as system-deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ ffmpeg libsndfile1 portaudio19-dev python3-dev curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# All dependencies with BuildKit cache mount (FAST rebuilds!)
FROM system-deps as python-deps
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# NLTK data layer
FROM python-deps as nltk-data
RUN python -c "import nltk; nltk.download('punkt_tab', download_dir='/usr/local/nltk_data', quiet=True)"

# PyTorch models with better error handling
FROM nltk-data as torch-models
RUN mkdir -p /opt/torch && \
    python -c "import torch; torch.hub.set_dir('/opt/torch'); torch.hub.load('snakers4/silero-vad', 'silero_vad', force_reload=True, trust_repo=True)" && \
    echo "✅ Silero VAD models downloaded successfully"

# Application layer (copy code LAST for best caching)
FROM torch-models as app
WORKDIR /app

# Create directories
RUN mkdir -p logs recordings data

# Copy application code (most frequently changing layer - do LAST)
COPY config/ ./config/
COPY flows/ ./flows/
COPY models/ ./models/
COPY pipeline/ ./pipeline/
COPY services/ ./services/
COPY utils/ ./utils/
COPY data/ ./data/
COPY bot.py ./
COPY chat_service.py ./

# Create non-root user and set permissions
RUN groupadd -r pipecat && useradd -r -g pipecat pipecat && \
    chown -R pipecat:pipecat /app && \
    chown -R pipecat:pipecat /opt/torch

# Switch to non-root user
USER pipecat

# Faster health check
HEALTHCHECK --interval=15s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:$PORT/health || exit 1

EXPOSE $PORT

# Optimized uvicorn with timeout settings (14 concurrent calls per agent = 42 total capacity)
CMD ["python", "-m", "uvicorn", "bot:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "--backlog", "2048", "--limit-concurrency", "14", "--timeout-keep-alive", "30"]
