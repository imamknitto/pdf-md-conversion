from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
from fastapi import HTTPException
from PIL import Image as _Image

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

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/tiff",
    "image/tif",
    "application/octet-stream",
    "",
}

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_IMAGE_CONTENT_TYPES",
    "ALLOWED_IMAGE_SUFFIXES",
    "_require_token",
    "_validate_upload",
    "_validate_image_upload",
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


def _validate_image_upload(filename: str, content_type: str, data: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Endpoint /ocr hanya mendukung gambar "
                f"({', '.join(sorted(ALLOWED_IMAGE_SUFFIXES))})."
            ),
        )
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"content-type tidak didukung: {content_type}",
        )
    if len(data) > config.MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {config.MAX_IMAGE_MB} MB.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="File gambar kosong.")
    try:
        with _Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="File bukan gambar yang valid.") from None


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
