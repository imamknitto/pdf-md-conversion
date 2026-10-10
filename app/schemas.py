from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ImageMeta(BaseModel):
    page: int
    kind: str  # "image" | "scan"
    filename: str
    url: str
    width: int
    height: int
    size_bytes: int
    ocr_text: Optional[str] = None
    flowchart_mermaid: Optional[str] = None


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
    expires_at: Optional[str] = None


class ConvertJobAccepted(BaseModel):
    job_id: str
    doc_id: str
    status: str = "queued"


class ConvertJobProgress(BaseModel):
    pages_done: int = 0
    pages_total: Optional[int] = None


class ConvertJobStatus(BaseModel):
    job_id: str
    doc_id: str
    filename: Optional[str] = None
    status: str  # queued | processing | done | error
    progress: Optional[ConvertJobProgress] = None
    result: Optional[ConvertResponse] = None
    error: Optional[str] = None
