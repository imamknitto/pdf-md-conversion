from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address

from ...schemas import ConvertResponse
from ...services.conversion_service import convert_image
from ...storage import storage
from ..deps import _require_token, _validate_image_upload, _write_meta_and_response

router = APIRouter()

logger = logging.getLogger("pdf-md-conversion")

limiter = Limiter(key_func=get_remote_address)


def _rate_limit_key(request: Request) -> str:
    """Rate limit key: API key if authenticated, else IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return f"apikey:{auth[7:]}"
    return get_remote_address(request)


@router.post("/ocr", response_model=ConvertResponse)
@limiter.limit("10/minute", key_func=_rate_limit_key)
async def ocr_standalone(
    request: Request,
    file: UploadFile = File(...),
    _auth: None = Depends(_require_token),
) -> ConvertResponse:
    filename = file.filename or "image.png"
    data = await file.read()
    _validate_image_upload(filename, file.content_type or "", data)
    logger.info("ocr sync start file=%s bytes=%d", filename, len(data))

    doc_id = storage.new_doc_id()
    try:
        result = await asyncio.to_thread(
            convert_image,
            image_bytes=data,
            doc_id=doc_id,
            filename=filename,
        )
    except Exception:
        logger.exception("image OCR failed")
        storage.remove_doc(doc_id)
        raise HTTPException(status_code=500, detail="Gagal memproses gambar.") from None

    logger.info(
        "ocr sync done doc=%s images_kept=%d",
        doc_id,
        result.images_kept,
    )
    return _write_meta_and_response(result, doc_id, filename)
