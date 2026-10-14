from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.main import app
from app.services import job_service as jobs

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "sample-doc" / "flowchart-pdf.pdf"


def _clear_jobs_db() -> None:
    """Clear all jobs from SQLite database and reset shutdown state."""
    jobs._DB_PATH = None
    db_path = config.DATA_DIR / "jobs.sqlite"
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM jobs")
            conn.commit()
    jobs._reset_shutdown_state()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_MD_CONVERTER_API_KEY", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    jobs._DB_PATH = None
    _clear_jobs_db()
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
    return SAMPLE_PDF.read_bytes()
