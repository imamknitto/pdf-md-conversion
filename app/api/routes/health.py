from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

SERVICE_VERSION = "0.1.0"


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pdf-md-conversion", "version": SERVICE_VERSION}
