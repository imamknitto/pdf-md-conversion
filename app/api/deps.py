from __future__ import annotations

import io
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
from fastapi import HTTPException, UploadFile
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
    "_save_pdf_upload",
    "_validate_image_upload",
    "_write_meta_and_response",
]

_CHUNK_SIZE = 1024 * 1024


def _inspect_pdf(source: bytes | Path) -> int:
    """Open the PDF once: reject password-protected files, return page count.

    ``needs_pass`` is the authoritative signal that content cannot be read;
    ``is_encrypted`` also covers PDFs encrypted with an empty user password.
    Returns 0 when the file cannot be parsed (magic bytes already checked).
    """
    try:
        if isinstance(source, Path):
            doc_ctx = pymupdf.open(source)
        else:
            doc_ctx = pymupdf.open(stream=source, filetype="pdf")
        with doc_ctx as doc:
            if doc.needs_pass or doc.is_encrypted:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "PDF terenkripsi (password protected). "
                        "Harap hapus password sebelum upload."
                    ),
                )
            return doc.page_count
    except HTTPException:
        raise
    except Exception:
        return 0


def _validate_pdf_file(path: Path, size: int) -> None:
    """Validate an already-streamed PDF from disk (no full-file read)."""
    if size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {config.MAX_UPLOAD_MB} MB.",
        )
    with path.open("rb") as fh:
        if fh.read(4) != b"%PDF":
            raise HTTPException(status_code=400, detail="File bukan PDF yang valid.")
    pages = _inspect_pdf(path)
    if pages > config.MAX_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"PDF melebihi batas {config.MAX_PAGES} halaman ({pages}).",
        )


async def _save_pdf_upload(file: UploadFile, filename: str, content_type: str) -> Path:
    """Stream an upload to a temp file, validating size/magic/page count.

    Never holds the whole document in memory. The caller owns the returned
    path and must unlink it in a ``finally`` block.
    """
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="v1 hanya mendukung file PDF.")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"content-type tidak didukung: {content_type}")

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    size = 0
    try:
        with tmp:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Ukuran file melebihi batas {config.MAX_UPLOAD_MB} MB.",
                    )
                tmp.write(chunk)
        _validate_pdf_file(tmp_path, size)
        return tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


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
            width, height = img.size
            img.verify()
    except _Image.DecompressionBombError:
        raise HTTPException(
            status_code=413,
            detail=f"Resolusi gambar melebihi batas {config.MAX_IMAGE_PIXELS} piksel.",
        ) from None
    except Exception:
        raise HTTPException(status_code=400, detail="File bukan gambar yang valid.") from None
    if width * height > config.MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=f"Resolusi gambar melebihi batas {config.MAX_IMAGE_PIXELS} piksel.",
        )


def _write_meta_and_response(result, doc_id: str) -> ConvertResponse:
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
