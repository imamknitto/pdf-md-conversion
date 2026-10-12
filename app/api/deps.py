from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from ..core import config
from ..core.security import _require_token
from ..schemas import ConvertResponse, ConvertStats, ImageMeta
from ..storage import storage

logger = logging.getLogger("doc-md-conversion")

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


def _validate_upload(filename: str, content_type: str, data: bytes) -> None:
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="v1 hanya mendukung file PDF.")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"content-type tidak didukung: {content_type}"
        )
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {config.MAX_UPLOAD_MB} MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File bukan PDF yang valid.")


def _write_meta_and_response(
    result, doc_id: str, filename: str
) -> ConvertResponse:
    expires = datetime.now(timezone.utc) + timedelta(minutes=config.PUBLIC_TTL_MINUTES)
    stats = ConvertStats(
        pages=result.pages,
        scanned_pages=result.scanned_pages,
        tables=result.tables,
        images_kept=result.images_kept,
        images_skipped=result.images_skipped,
    )
    try:
        (storage.safe_doc_dir(doc_id) / "meta.json").write_text(
            json.dumps(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "markdown_url": storage.markdown_url(doc_id),
                    "stats": stats.model_dump(),
                    "warnings": result.warnings,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": expires.isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("gagal menulis meta.json untuk %s", doc_id)
    return ConvertResponse(
        doc_id=result.doc_id,
        markdown=result.markdown,
        markdown_url=storage.markdown_url(doc_id),
        stats=stats,
        images=[ImageMeta(**img) for img in result.images],
        warnings=result.warnings,
        expires_at=expires.isoformat(),
    )
