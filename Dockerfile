FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PUBLIC_DIR=/data/public

WORKDIR /srv

# System libs needed at runtime (libgomp for onnxruntime, libgl for pymupdf/pillow funnel)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app/ ./app/

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data/public && useradd -m -u 10001 appuser && chown -R appuser:appuser /srv /data
USER appuser

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Single worker: OCR engine is an in-process singleton
# Default port 8100: :8000 belongs to the public gateway in every environment.
# Binds 0.0.0.0 for container networking; the image publishes no port and the
# service is only reachable inside the private network (plus bearer token).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
