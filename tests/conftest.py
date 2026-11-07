from __future__ import annotations

import functools
import io
import os
import sqlite3

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import config
from app.core.rate_limit import limiter
from app.main import app
from app.services import job_service as jobs


def _clear_jobs_db() -> None:
    """Clear all jobs from SQLite database and reset in-memory job state."""
    jobs._DB_PATH = None
    jobs._initialized_paths.clear()
    jobs._last_sweep = 0.0
    db_path = config.DATA_DIR / "jobs.sqlite"
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM jobs")
            conn.commit()
    jobs._reset_shutdown_state()


@functools.lru_cache(maxsize=1)
def _build_sample_pdf() -> bytes:
    """Build the sample PDF programmatically (no local sample file required)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Sample document for conversion tests. This paragraph is intentionally "
        "long enough to exceed the minimum text threshold, so the page is "
        "treated as a text page rather than a scan.",
        fontsize=11,
    )
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(200, 50, 50)).save(buf, format="PNG")
    page.insert_image(pymupdf.Rect(72, 160, 172, 260), stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_MD_CONVERTER_API_KEY", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    jobs._DB_PATH = None
    _clear_jobs_db()
    limiter.reset()
    with TestClient(app) as c:
        yield c
    _clear_jobs_db()


@pytest.fixture()
def authed_client(client, monkeypatch):
    monkeypatch.setenv("PDF_MD_CONVERTER_API_KEY", "test-key-123")
    return client


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('PDF_MD_CONVERTER_API_KEY', '')}"}


def sample_bytes() -> bytes:
    return _build_sample_pdf()
