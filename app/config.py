from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 30)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

PUBLIC_DIR = Path(os.getenv("PUBLIC_DIR", str(BASE_DIR / "public")))

PUBLIC_TTL_MINUTES = _int("PUBLIC_TTL_MINUTES", 60)
PUBLIC_MAX_MB = _int("PUBLIC_MAX_MB", 1024)
PUBLIC_MAX_BYTES = PUBLIC_MAX_MB * 1024 * 1024
CLEANUP_INTERVAL_MINUTES = _int("CLEANUP_INTERVAL_MINUTES", 10)

MIN_TEXT_CHARS = _int("MIN_TEXT_CHARS", 50)
PAGE_COVERAGE = _float("PAGE_COVERAGE", 0.5)
RECURRING_XREF_RATIO = _float("RECURRING_XREF_RATIO", 0.5)
IMAGE_MIN_AREA_RATIO = _float("IMAGE_MIN_AREA_RATIO", 0.01)
MAX_IMAGE_BYTES = _int("MAX_IMAGE_MB", 10) * 1024 * 1024
MAX_IMAGES_PER_DOC = _int("MAX_IMAGES_PER_DOC", 200)

OCR_DPI = _int("OCR_DPI", 200)
OCR_MIN_SIDE = _int("OCR_MIN_SIDE", 1200)
OCR_MAX_SCALE = _float("OCR_MAX_SCALE", 2.0)
OCR_MAX_PIXELS = _int("OCR_MAX_PIXELS", 9000000)
# Cost guards: single-worker CPU saturates at ~3-4s per full-page OCR.
# - OCR_MAX_SIDE: downscale images whose longest side exceeds this (px).
# - OCR_SKIP_BELOW: skip OCR on images smaller than this (px, likely icons).
# - OCR_MAX_IMAGES: max OCR'd figures per doc; rest are kept without OCR text.
OCR_MAX_SIDE = _int("OCR_MAX_SIDE", 1600)
OCR_SKIP_BELOW = _int("OCR_SKIP_BELOW", 100)
OCR_MAX_IMAGES = _int("OCR_MAX_IMAGES", 100)

FLOWCHART_ENABLE = _bool("FLOWCHART_ENABLE", True)
FLOWCHART_Y_TOL = _float("FLOWCHART_Y_TOL", 10.0)
FLOWCHART_MAX_SHAPES = _int("FLOWCHART_MAX_SHAPES", 200)
FLOWCHART_SCORE_MIN = _int("FLOWCHART_SCORE_MIN", 2)

# Async conversion jobs (POST /convert/jobs): how long finished/unfinished
# job state is kept, and how many unfinished jobs may queue on the single
# worker. Extra submissions get HTTP 429 while the queue is full.
CONVERT_JOB_TTL_MINUTES = _int("CONVERT_JOB_TTL_MINUTES", 120)
CONVERT_MAX_QUEUE = _int("CONVERT_MAX_QUEUE", 4)
