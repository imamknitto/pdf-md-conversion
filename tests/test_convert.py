from __future__ import annotations

import base64

from app.core import config
from tests.conftest import sample_bytes


def test_convert_sample_structure(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 200
    data = r.json()
    assert sorted(data.keys()) == sorted(
        [
            "doc_id",
            "markdown",
            "markdown_url",
            "stats",
            "images",
            "warnings",
            "expires_at",
            "metadata",
        ]
    )
    assert sorted(data["stats"].keys()) == sorted(
        ["pages", "scanned_pages", "tables", "images_kept", "images_skipped"]
    )
    assert data["stats"]["pages"] == 1
    assert data["markdown"].startswith("## Page 1")
    assert data["markdown_url"] == f"/public/{data['doc_id']}/doc.md"


def test_convert_extracts_images(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"extract_images": "true"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["images_kept"] >= 1
    assert "![" in data["markdown"]
    assert data["images"]
    for img in data["images"]:
        assert img["data_base64"]
        assert len(base64.b64decode(img["data_base64"])) == img["size_bytes"]
        assert img["url"].startswith(f"/public/{data['doc_id']}/")
        assert not img["url"].startswith("data:")
    assert not (config.DATA_DIR / data["doc_id"]).exists()


def test_convert_rejects_non_pdf_suffix(client):
    r = client.post("/v1/convert", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_convert_rejects_bad_content_type(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("a.pdf", b"%PDF-1.4 x", "text/plain")},
    )
    assert r.status_code == 400


def test_convert_rejects_invalid_magic(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("a.pdf", b"not a pdf", "application/pdf")},
    )
    assert r.status_code == 400


def test_convert_rejects_oversize(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 413


def test_convert_rejects_bad_ocr_scope(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"ocr_scope": "bogus"},
    )
    assert r.status_code == 400
