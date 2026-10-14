from __future__ import annotations

from pydantic import BaseModel


class ImageMeta(BaseModel):
    page: int
    kind: str  # "image" | "scan"
    filename: str
    url: str
    width: int
    height: int
    size_bytes: int
    ocr_text: str | None = None
    flowchart_mermaid: str | None = None
    data_base64: str | None = None


class ConvertStats(BaseModel):
    pages: int
    scanned_pages: int
    tables: int
    images_kept: int
    images_skipped: int


class ConvertResponse(BaseModel):
    doc_id: str
    markdown: str
    markdown_url: str
    stats: ConvertStats
    images: list[ImageMeta]
    warnings: list[str]
    expires_at: str | None = None
    metadata: dict = {}


class ConvertJobAccepted(BaseModel):
    job_id: str
    doc_id: str
    status: str = "queued"


class ConvertJobProgress(BaseModel):
    pages_done: int = 0
    pages_total: int | None = None


class ConvertJobStatus(BaseModel):
    job_id: str
    doc_id: str
    filename: str | None = None
    status: str  # queued | processing | done | error | cancelled
    progress: ConvertJobProgress | None = None
    result: ConvertResponse | None = None
    error: str | None = None
