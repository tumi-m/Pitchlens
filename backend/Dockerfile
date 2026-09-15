FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry==1.8.3
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only=main --no-root && rm -rf /tmp/poetry_cache

COPY app/ ./app/

RUN useradd --create-home --shell /bin/bash pitchlens && chown -R pitchlens:pitchlens /app
USER pitchlens
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://127.0.0.1:${PORT:-8080}/api/v1/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
