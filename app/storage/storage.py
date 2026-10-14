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
    """Per-doc path under DATA_DIR (legacy cleanup only; artifacts are not stored here)."""
    if not _DOC_ID_RE.match(doc_id or ""):
        raise ValueError("invalid doc_id")
    return config.DATA_DIR / doc_id


def public_url(doc_id: str, kind: str, filename: str) -> str:
    """Relative URL for the consumer to serve; not a path on this service."""
    return f"/public/{doc_id}/{kind}/{filename}"


def markdown_url(doc_id: str) -> str:
    """Relative markdown URL for the consumer; not a path on this service."""
    return f"/public/{doc_id}/{MARKDOWN_FILENAME}"


def remove_doc(doc_id: str) -> None:
    """Best-effort cleanup of leftover per-doc dirs under DATA_DIR (no-op when absent)."""
    try:
        shutil.rmtree(safe_doc_dir(doc_id), ignore_errors=True)
    except ValueError:
        pass
