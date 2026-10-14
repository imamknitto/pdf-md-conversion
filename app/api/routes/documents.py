from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import _require_token

router = APIRouter()


@router.get("/documents")
def list_documents(_auth: None = Depends(_require_token)) -> dict:
    """Legacy: artifacts live on the consumer; converter never lists files."""
    return {"documents": []}


@router.get("/download/{doc_id}")
def download_markdown(doc_id: str, _auth: None = Depends(_require_token)) -> None:
    """Legacy: markdown is returned in convert/job responses, not as files here."""
    raise HTTPException(
        status_code=404,
        detail="Markdown tidak disimpan di converter; gunakan field markdown dari hasil konversi.",
    )


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, _auth: None = Depends(_require_token)) -> None:
    """Legacy: artifact deletion is owned by the consumer."""
    raise HTTPException(
        status_code=404,
        detail="Dokumen tidak disimpan di converter; hapus artefak di consumer.",
    )
