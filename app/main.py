from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from . import config, storage
from .cleanup import cleanup_loop, sweep_once
from .converter import convert_pdf
from .schemas import ConvertResponse, ConvertStats, ImageMeta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("markitdown")

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


app = FastAPI(title="markitdown-api", version="0.1.0", lifespan=lifespan)

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
        "PLATFORM_URL", "http://127.0.0.1:5173,http://127.0.0.1:3000"
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
    return {"status": "ok", "service": "md-conversion-service-py", "version": SERVICE_VERSION}


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


@app.post("/convert", response_model=ConvertResponse)
async def convert(
    file: UploadFile = File(...),
    scan_images: bool = Form(False),
    _auth: None = Depends(_require_token),
) -> ConvertResponse:
    filename = file.filename or "upload.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="v1 hanya mendukung file PDF.")
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"content-type tidak didukung: {file.content_type}")

    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file melebihi batas {config.MAX_UPLOAD_MB} MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File bukan PDF yang valid.")

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(data)
    tmp.close()
    tmp_path = Path(tmp.name)

    doc_id = storage.new_doc_id()
    try:
        result = convert_pdf(
            pdf_path=tmp_path,
            doc_id=doc_id,
            scan_images=scan_images,
            extract_images=True,
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

    expires = datetime.now(timezone.utc) + timedelta(minutes=config.PUBLIC_TTL_MINUTES)

    return ConvertResponse(
        doc_id=result.doc_id,
        markdown=result.markdown,
        markdown_url=storage.markdown_url(doc_id),
        stats=ConvertStats(
            pages=result.pages,
            scanned_pages=result.scanned_pages,
            tables=result.tables,
            images_kept=result.images_kept,
            images_skipped=result.images_skipped,
        ),
        images=[ImageMeta(**img) for img in result.images],
        warnings=result.warnings,
        expires_at=expires.isoformat(),
    )
