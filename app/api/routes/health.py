from __future__ import annotations

from fastapi import APIRouter

from ...core.version import __version__

router = APIRouter()

SERVICE_VERSION = __version__


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pdf-md-conversion", "version": SERVICE_VERSION}
