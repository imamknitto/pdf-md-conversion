from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, jobs
from app.main import app

SAMPLE_PDF = Path(__file__).resolve().parent.parent / "sample-doc" / "flowchart-pdf.pdf"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERTER_API_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_DIR", tmp_path / "public")
    (tmp_path / "public").mkdir(parents=True, exist_ok=True)
    jobs._jobs.clear()
    with TestClient(app) as c:
        yield c
    jobs._jobs.clear()


@pytest.fixture()
def authed_client(client, monkeypatch):
    monkeypatch.setenv("CONVERTER_API_KEY", "test-key-123")
    return client


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('CONVERTER_API_KEY', '')}"}


def sample_bytes() -> bytes:
    return SAMPLE_PDF.read_bytes()
