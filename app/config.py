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
