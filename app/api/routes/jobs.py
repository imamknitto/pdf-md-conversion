from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from ...core.errors import JobCancelled
from ...core.rate_limit import limiter, rate_limit_key
from ...schemas import ConvertJobAccepted, ConvertJobProgress, ConvertJobStatus
from ...services import job_service as jobs
from ...services.conversion_service import convert_pdf
from ...storage import storage
from ..deps import _require_token, _save_pdf_upload, _write_meta_and_response

router = APIRouter()

logger = logging.getLogger("pdf-md-conversion")

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class _JobWork:
    """Payload handed from the request handler to the single worker thread."""

    job_id: str
    doc_id: str
    tmp_path: Path
    filename: str
    options: dict


_work_queue: queue.Queue[_JobWork | None] = queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()
_worker_stop = threading.Event()


def start_worker() -> None:
    """Start the single conversion worker (idempotent)."""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_stop.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="convert-worker",
            daemon=True,
        )
        _worker_thread.start()
    logger.info("convert worker started")


def stop_worker(timeout: float = 300.0) -> bool:
    """Stop accepting work, finish the active job, and join the worker."""
    global _worker_thread
    _worker_stop.set()
    with _worker_lock:
        thread = _worker_thread
        _worker_thread = None
    if thread is None:
        return True
    thread.join(timeout)
    if thread.is_alive():
        logger.warning("convert worker did not stop within %.1fs", timeout)
        return False
    logger.info("convert worker stopped")
    return True


def _enqueue(work: _JobWork) -> bool:
    if jobs.is_shutting_down() or _worker_stop.is_set():
        return False
    _work_queue.put(work)
    return True


def _worker_loop() -> None:
    while True:
        try:
            work = _work_queue.get(timeout=0.1)
        except queue.Empty:
            if _worker_stop.is_set():
                return
            continue
        try:
            if _worker_stop.is_set():
                jobs.set_error(work.job_id, "Interrupted by service shutdown")
                storage.remove_doc(work.doc_id)
                continue
            _execute_job(work)
        finally:
            _work_queue.task_done()


def _finish_cancelled(job_id: str, doc_id: str) -> None:
    """Clean up after a cancellation without leaving the job non-terminal."""
    jobs.clear_cancelled_flag(job_id)
    storage.remove_doc(doc_id)
    job = jobs.get_job(job_id)
    if job and job["status"] not in (jobs.DONE, jobs.ERROR, jobs.CANCELLED):
        jobs.update_job(job_id, status=jobs.CANCELLED, error="Dibatalkan oleh user")


def _execute_job(work: _JobWork) -> None:
    """Run one async conversion on the worker thread; never raises."""
    job_id = work.job_id
    doc_id = work.doc_id
    tmp_path = work.tmp_path
    job: dict | None = None
    try:
        job = jobs.get_job(job_id)
        if job is None:
            storage.remove_doc(doc_id)
            return
        if job["status"] in (jobs.CANCELLED, jobs.DONE, jobs.ERROR):
            if job["status"] == jobs.CANCELLED:
                jobs.clear_cancelled_flag(job_id)
                storage.remove_doc(doc_id)
            return
        if jobs.is_cancelled(job_id):
            _finish_cancelled(job_id, doc_id)
            return

        options = work.options
        jobs.update_job(job_id, status=jobs.PROCESSING)
        logger.info(
            "convert job start job=%s doc=%s file=%s",
            job_id,
            doc_id,
            work.filename,
        )

        last_progress = {"time": 0.0}

        def on_page(done: int, total: int) -> None:
            if jobs.is_cancelled(job_id):
                raise JobCancelled("Job cancelled")
            now = time.monotonic()
            step = max(1, total // 20)
            if done == total or done % step == 0 or now - last_progress["time"] >= 1.0:
                jobs.update_job(job_id, pages_done=done, pages_total=total)
                last_progress["time"] = now

        result = convert_pdf(
            pdf_path=tmp_path,
            doc_id=doc_id,
            scan_images=options.get("scan_images", False),
            extract_images=options.get("extract_images", True),
            ocr_images=options.get("ocr_images", False),
            ocr_scope=options.get("ocr_scope", "all"),
            on_page=on_page,
            check_cancelled=lambda: jobs.is_cancelled(job_id),
        )

        # Cancellation can land between the last page and result persistence.
        if jobs.is_cancelled(job_id):
            raise JobCancelled("Job cancelled")

        response = _write_meta_and_response(result, doc_id)
        jobs.set_result(job_id, response.model_dump())
        jobs.clear_cancelled_flag(job_id)
        logger.info(
            "convert job done job=%s doc=%s pages=%d images_kept=%d",
            job_id,
            doc_id,
            result.pages,
            result.images_kept,
        )
        done_record = {
            **job,
            "status": jobs.DONE,
            "pages_done": result.pages,
            "pages_total": result.pages,
            "result": response.model_dump(),
            "error": None,
        }
        jobs.deliver_webhook(job_id, jobs.DONE, job=done_record)
    except JobCancelled:
        logger.info("convert job cancelled job=%s", job_id)
        _finish_cancelled(job_id, doc_id)
    except Exception:
        if jobs.is_cancelled(job_id):
            # Never overwrite a cancel with an error row.
            logger.info("convert job cancelled during failure job=%s", job_id)
            _finish_cancelled(job_id, doc_id)
            return
        logger.exception("async conversion failed job=%s", job_id)
        storage.remove_doc(doc_id)
        jobs.set_error(job_id, "Gagal memproses PDF.")
        error_record = None
        if job:
            error_record = {**job, "status": jobs.ERROR, "error": "Gagal memproses PDF."}
        jobs.deliver_webhook(job_id, jobs.ERROR, job=error_record)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@router.post("/convert/jobs", response_model=ConvertJobAccepted, status_code=202)
@limiter.limit("20/minute", key_func=rate_limit_key)
async def convert_async(
    request: Request,
    file: UploadFile = File(...),
    scan_images: bool = Form(False),
    extract_images: bool = Form(True),
    ocr_images: bool = Form(False),
    ocr_scope: str = Form("all"),
    webhook_url: str | None = Form(None),
    _auth: None = Depends(_require_token),
) -> ConvertJobAccepted:
    """Accept a conversion as a background job (for large PDFs).

    Returns 202 immediately; poll GET /convert/jobs/{job_id}. A single worker
    thread processes one job at a time (OCR engine is a singleton), so
    additional jobs stay `queued` until it is their turn.
    """
    if ocr_scope not in ("all", "flowchart"):
        raise HTTPException(
            status_code=400,
            detail='ocr_scope harus "all" atau "flowchart".',
        )
    webhook_url = (webhook_url or "").strip() or None
    if webhook_url:
        try:
            # DNS resolution blocks, so keep it off the event loop.
            await asyncio.to_thread(jobs.validate_webhook_url, webhook_url)
        except jobs.WebhookUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
    filename = file.filename or "upload.pdf"
    tmp_path = await _save_pdf_upload(file, filename, file.content_type or "")

    doc_id = storage.new_doc_id()
    options = {
        "scan_images": scan_images,
        "extract_images": extract_images,
        "ocr_images": ocr_images,
        "ocr_scope": ocr_scope,
    }
    if webhook_url:
        options["webhook_url"] = webhook_url
    job = jobs.create_job(doc_id, filename, options)
    if job is None:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=429,
            detail="Antrean konversi penuh, coba lagi sebentar.",
        )
    work = _JobWork(
        job_id=job["job_id"],
        doc_id=doc_id,
        tmp_path=tmp_path,
        filename=filename,
        options=options,
    )
    if not _enqueue(work):
        tmp_path.unlink(missing_ok=True)
        jobs.set_error(job["job_id"], "Service shutting down")
        raise HTTPException(
            status_code=429,
            detail="Service sedang shutdown, coba lagi sebentar.",
        )
    return ConvertJobAccepted(job_id=job["job_id"], doc_id=doc_id, status=jobs.QUEUED)


@router.get("/convert/jobs/{job_id}", response_model=ConvertJobStatus)
def convert_job_status(job_id: str, _auth: None = Depends(_require_token)) -> ConvertJobStatus:
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(status_code=404, detail="job_id tidak valid.")
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan / sudah expired.")
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


@router.delete("/convert/jobs/{job_id}", status_code=204)
def convert_job_cancel(job_id: str, _auth: None = Depends(_require_token)) -> None:
    """Cancel a queued or processing conversion job.

    - Queued: removed from queue, artifacts cleaned up
    - Processing: cooperative cancel (worker checks flag per page), artifacts cleaned up
    - Done/Error: returns 409 (cannot cancel terminal state)
    - Not found/expired: returns 404
    """
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(status_code=404, detail="job_id tidak valid.")

    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan / sudah expired.")

    if job["status"] in (jobs.DONE, jobs.ERROR):
        raise HTTPException(
            status_code=409,
            detail=f"Tidak bisa membatalkan job dengan status {job['status']}.",
        )

    cancelled = jobs.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan / sudah expired.")

    logger.info("convert job cancelled by user job=%s", job_id)
