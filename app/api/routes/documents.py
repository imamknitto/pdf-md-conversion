from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ...core import config
from ...storage import storage
from ..deps import _require_token

router = APIRouter()


@router.get("/documents")
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


@router.get("/download/{doc_id}")
def download_markdown(doc_id: str, _auth: None = Depends(_require_token)) -> FileResponse:
    """Unduh markdown sebagai file text/markdown (tanpa escape JSON)."""
    try:
        path = storage.safe_doc_dir(doc_id) / storage.MARKDOWN_FILENAME
    except ValueError:
        raise HTTPException(status_code=404, detail="doc_id tidak valid.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Markdown tidak ditemukan / sudah expired.")
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{doc_id}.md",
    )


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, _auth: None = Depends(_require_token)) -> dict:
    """Hapus dokumen hasil konversi beserta seluruh artefaknya."""
    try:
        doc_dir = storage.safe_doc_dir(doc_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="doc_id tidak valid.")
    if not (doc_dir / storage.MARKDOWN_FILENAME).is_file():
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan / sudah expired.")
    storage.remove_doc(doc_id)
    return {"doc_id": doc_id, "deleted": True}
