from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from . import config, jobs, storage
from .cleanup import cleanup_loop, sweep_once
from .converter import convert_pdf
from .schemas import (
    ConvertJobAccepted,
    ConvertJobProgress,
    ConvertJobStatus,
    ConvertResponse,
    ConvertStats,
    ImageMeta,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("doc-md-conversion")

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    sweep_once()
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="doc-md-conversion", version="0.1.0", lifespan=lifespan)

SERVICE_VERSION = "0.1.0"

_bearer = HTTPBearer(auto_error=False)


def _require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Bearer auth, active only when CONVERTER_API_KEY is set."""
    expected = os.getenv("CONVERTER_API_KEY", "")
    if not expected:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Unauthorized.")


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "PLATFORM_URL",
        "http://127.0.0.1:8378,http://localhost:8378,http://127.0.0.1:8377,http://localhost:8377",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

config.PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/public", StaticFiles(directory=str(config.PUBLIC_DIR)), name="public")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "doc-md-conversion", "version": SERVICE_VERSION}


@app.get("/documents")
def list_documents(_auth: None = Depends(_require_token)) -> dict:
    """Daftar dokumen hasil konversi yang masih tersedia (terbaru dulu)."""
    try:
        entries = sorted(
            (p for p in config.PUBLIC_DIR.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except FileNotFoundError:
        return {"documents": []}
    documents = []
    for entry in entries:
        try:
            doc_dir = storage.safe_doc_dir(entry.name)
        except ValueError:
            continue
        if not (doc_dir / storage.MARKDOWN_FILENAME).is_file():
            continue
        item: dict = {
            "doc_id": entry.name,
            "markdown_url": storage.markdown_url(entry.name),
        }
        meta_path = doc_dir / "meta.json"
        try:
            item.update(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            item["created_at"] = datetime.fromtimestamp(
                entry.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        documents.append(item)
    return {"documents": documents}


@app.get("/download/{doc_id}")
def download_markdown(
    doc_id: str, _auth: None = Depends(_require_token)
) -> FileResponse:
    """Unduh markdown sebagai file text/markdown (tanpa escape JSON)."""
    try:
        path = storage.safe_doc_dir(doc_id) / storage.MARKDOWN_FILENAME
    except ValueError:
        raise HTTPException(status_code=404, detail="doc_id tidak valid.")
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail="Markdown tidak ditemukan / sudah expired."
        )
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{doc_id}.md",
    )


@app.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str, _auth: None = Depends(_require_token)
) -> dict:
    """Hapus dokumen hasil konversi beserta seluruh artefaknya."""
    try:
        doc_dir = storage.safe_doc_dir(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="doc_id tidak valid.")
    if not (doc_dir / storage.MARKDOWN_FILENAME).is_file():
        raise HTTPException(
            status_code=404, detail="Dokumen tidak ditemukan / sudah expired."
        )
    storage.remove_doc(doc_id)
    return {"doc_id": doc_id, "deleted": True}


_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


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


@app.post("/convert", response_model=ConvertResponse)
async def convert(
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
    data = await file.read()
    _validate_upload(filename, file.content_type or "", data)
    logger.info("convert sync start file=%s bytes=%d", filename, len(data))

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(data)
    tmp.close()
    tmp_path = Path(tmp.name)

    doc_id = storage.new_doc_id()
    try:
        # convert_pdf is CPU/sync-blocking; offload to a thread so the
        # single-worker event loop stays responsive (/health, /documents).
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
        try:
            tmp_path.unlink()
        except OSError:
            pass

    logger.info(
        "convert sync done doc=%s pages=%d images_kept=%d",
        doc_id,
        result.pages,
        result.images_kept,
    )
    return _write_meta_and_response(result, doc_id, filename)


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


@app.post("/convert/jobs", response_model=ConvertJobAccepted, status_code=202)
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


@app.get("/convert/jobs/{job_id}", response_model=ConvertJobStatus)
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
