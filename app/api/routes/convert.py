from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from ...core.rate_limit import limiter, rate_limit_key
from ...schemas import ConvertResponse
from ...services.conversion_service import convert_pdf
from ...storage import storage
from ..deps import _require_token, _save_pdf_upload, _write_meta_and_response

router = APIRouter()

logger = logging.getLogger("pdf-md-conversion")


@router.post("/convert", response_model=ConvertResponse)
@limiter.limit("10/minute", key_func=rate_limit_key)
async def convert(
    request: Request,
    file: UploadFile = File(...),
    scan_images: bool = Form(False),
    extract_images: bool = Form(True),
    ocr_images: bool = Form(False),
    ocr_scope: str = Form("all"),
    _auth: None = Depends(_require_token),
) -> ConvertResponse:
    if ocr_scope not in ("all", "flowchart"):
        raise HTTPException(
            status_code=400,
            detail='ocr_scope harus "all" atau "flowchart".',
        )
    filename = file.filename or "upload.pdf"
    tmp_path = await _save_pdf_upload(file, filename, file.content_type or "")
    logger.info("convert sync start file=%s", filename)

    doc_id = storage.new_doc_id()
    try:
        # convert_pdf is CPU/sync-blocking; offload to a thread so the
        # single-worker event loop stays responsive (/health).
        result = await asyncio.to_thread(
            convert_pdf,
            pdf_path=tmp_path,
            doc_id=doc_id,
            scan_images=scan_images,
            extract_images=extract_images,
            ocr_images=ocr_images,
            ocr_scope=ocr_scope,
        )
    except Exception:
        logger.exception("conversion failed")
        storage.remove_doc(doc_id)
        raise HTTPException(status_code=500, detail="Gagal memproses PDF.")
    finally:
        tmp_path.unlink(missing_ok=True)

    logger.info(
        "convert sync done doc=%s pages=%d images_kept=%d",
        doc_id,
        result.pages,
        result.images_kept,
    )
    return _write_meta_and_response(result, doc_id)
