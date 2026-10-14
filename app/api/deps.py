from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
from fastapi import HTTPException

from ..core import config
from ..core.security import _require_token
from ..schemas import ConvertResponse, ConvertStats, ImageMeta
from ..storage import storage

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "",
}

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "_require_token",
    "_validate_upload",
    "_write_meta_and_response",
]


def _is_pdf_encrypted(data: bytes) -> bool:
    """Check if PDF is password protected (encrypted)."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        return doc.is_encrypted
    except Exception:
        return False


def _validate_upload(filename: str, content_type: str, data: bytes) -> None:
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="v1 hanya mendukung file PDF.")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"content-type tidak didukung: {content_type}")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {config.MAX_UPLOAD_MB} MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File bukan PDF yang valid.")
    if _is_pdf_encrypted(data):
        raise HTTPException(
            status_code=400,
            detail="PDF terenkripsi (password protected). Harap hapus password sebelum upload.",
        )


def _write_meta_and_response(result, doc_id: str, _filename: str) -> ConvertResponse:
    expires = datetime.now(timezone.utc) + timedelta(minutes=config.PUBLIC_TTL_MINUTES)
    stats = ConvertStats(
        pages=result.pages,
        scanned_pages=result.scanned_pages,
        tables=result.tables,
        images_kept=result.images_kept,
        images_skipped=result.images_skipped,
    )
    return ConvertResponse(
        doc_id=result.doc_id,
        markdown=result.markdown,
        markdown_url=storage.markdown_url(doc_id),
        stats=stats,
        images=[ImageMeta(**img) for img in result.images],
        warnings=result.warnings,
        expires_at=expires.isoformat(),
        metadata=result.metadata,
    )
