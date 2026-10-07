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
