# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY run_local.py ./

# Unprivileged runtime user; only /app/data is writable.
RUN useradd --system --uid 10001 --home-dir /app appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000 8501

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Seed / migrate the lakehouse (read-write), then serve read-only.
CMD ["sh", "-c", "python run_local.py --check && DUCKDB_READ_ONLY=true exec python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --no-server-header"]
