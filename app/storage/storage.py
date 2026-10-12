from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from ..core import config

_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")

MARKDOWN_FILENAME = "doc.md"


def new_doc_id() -> str:
    return uuid.uuid4().hex


def safe_doc_dir(doc_id: str) -> Path:
    if not _DOC_ID_RE.match(doc_id or ""):
        raise ValueError("invalid doc_id")
    return config.PUBLIC_DIR / doc_id


def ensure_doc_dirs(doc_id: str) -> dict[str, Path]:
    base = safe_doc_dir(doc_id)
    images = base / "images"
    scans = base / "scans"
    images.mkdir(parents=True, exist_ok=True)
    scans.mkdir(parents=True, exist_ok=True)
    return {"base": base, "images": images, "scans": scans}


def write_bytes(directory: Path, filename: str, data: bytes) -> str:
    path = directory / filename
    path.write_bytes(data)
    return filename


def public_url(doc_id: str, kind: str, filename: str) -> str:
    return f"/public/{doc_id}/{kind}/{filename}"


def markdown_url(doc_id: str) -> str:
    return f"/public/{doc_id}/{MARKDOWN_FILENAME}"


def remove_doc(doc_id: str) -> None:
    try:
        shutil.rmtree(safe_doc_dir(doc_id), ignore_errors=True)
    except ValueError:
        pass
