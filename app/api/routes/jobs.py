from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from ...schemas import ConvertJobAccepted, ConvertJobProgress, ConvertJobStatus
from ...services import job_service as jobs
from ...services.conversion_service import convert_pdf
from ...storage import storage
from ..deps import _require_token, _validate_upload, _write_meta_and_response

router = APIRouter()

logger = logging.getLogger("doc-md-conversion")

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


async def _run_convert_job(
    job_id: str,
    doc_id: str,
    data: bytes,
    scan_images: bool,
    extract_images: bool,
    ocr_images: bool,
    ocr_scope: str,
    filename: str,
) -> None:
    """Background worker for one async job; never raises (records error)."""
    jobs.update_job(job_id, status=jobs.PROCESSING)
    logger.info(
        "convert job start job=%s doc=%s file=%s bytes=%d",
        job_id,
        doc_id,
        filename,
        len(data),
    )
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(data)
    tmp.close()
    tmp_path = Path(tmp.name)

    def on_page(done: int, total: int) -> None:
        jobs.update_job(job_id, pages_done=done, pages_total=total)

    try:
        result = await asyncio.to_thread(
            convert_pdf,
            pdf_path=tmp_path,
            doc_id=doc_id,
            scan_images=scan_images,
            extract_images=extract_images,
            ocr_images=ocr_images,
            ocr_scope=ocr_scope,
            on_page=on_page,
        )
    except Exception:
        logger.exception("async conversion failed job=%s", job_id)
        storage.remove_doc(doc_id)
        jobs.set_error(job_id, "Gagal memproses PDF.")
        return
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    response = _write_meta_and_response(result, doc_id, filename)
    jobs.set_result(job_id, response.model_dump())
    logger.info(
        "convert job done job=%s doc=%s pages=%d images_kept=%d",
        job_id,
        doc_id,
        result.pages,
        result.images_kept,
    )


@router.post("/convert/jobs", response_model=ConvertJobAccepted, status_code=202)
async def convert_async(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    scan_images: bool = Form(False),
    extract_images: bool = Form(True),
    ocr_images: bool = Form(False),
    ocr_scope: str = Form("all"),
    _auth: None = Depends(_require_token),
) -> ConvertJobAccepted:
    """Accept a conversion as a background job (for large PDFs).

    Returns 202 immediately; poll GET /convert/jobs/{job_id}. The heavy
    work runs in a thread so /health stays responsive on the single worker.
    """
    if ocr_scope not in ("all", "flowchart"):
        raise HTTPException(
            status_code=400,
            detail='ocr_scope harus "all" atau "flowchart".',
        )
    filename = file.filename or "upload.pdf"
    data = await file.read()
    _validate_upload(filename, file.content_type or "", data)

    doc_id = storage.new_doc_id()
    options = {
        "scan_images": scan_images,
        "extract_images": extract_images,
        "ocr_images": ocr_images,
        "ocr_scope": ocr_scope,
    }
    job = jobs.create_job(doc_id, filename, options)
    if job is None:
        raise HTTPException(
            status_code=429,
            detail="Antrean konversi penuh, coba lagi sebentar.",
        )
    background.add_task(
        _run_convert_job,
        job["job_id"],
        doc_id,
        data,
        scan_images,
        extract_images,
        ocr_images,
        ocr_scope,
        filename,
    )
    return ConvertJobAccepted(
        job_id=job["job_id"], doc_id=doc_id, status=jobs.QUEUED
    )


@router.get("/convert/jobs/{job_id}", response_model=ConvertJobStatus)
def convert_job_status(
    job_id: str, _auth: None = Depends(_require_token)
) -> ConvertJobStatus:
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(status_code=404, detail="job_id tidak valid.")
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail="Job tidak ditemukan / sudah expired."
        )
    return ConvertJobStatus(
        job_id=job["job_id"],
        doc_id=job["doc_id"],
        filename=job.get("filename"),
        status=job["status"],
        progress=ConvertJobProgress(
            pages_done=job.get("pages_done") or 0,
            pages_total=job.get("pages_total"),
        ),
        result=job.get("result"),
        error=job.get("error"),
    )
