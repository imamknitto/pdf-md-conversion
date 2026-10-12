from __future__ import annotations

from tests.conftest import sample_bytes


def test_convert_sample_structure(client):
    r = client.post(
        "/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 200
    data = r.json()
    assert sorted(data.keys()) == sorted(
        ["doc_id", "markdown", "markdown_url", "stats", "images", "warnings", "expires_at"]
    )
    assert sorted(data["stats"].keys()) == sorted(
        ["pages", "scanned_pages", "tables", "images_kept", "images_skipped"]
    )
    assert data["stats"]["pages"] == 1
    assert data["markdown"].startswith("## Page 1")
    assert data["markdown_url"] == f"/public/{data['doc_id']}/doc.md"


def test_convert_rejects_non_pdf_suffix(client):
    r = client.post("/convert", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_convert_rejects_bad_content_type(client):
    r = client.post(
        "/convert",
        files={"file": ("a.pdf", b"%PDF-1.4 x", "text/plain")},
    )
    assert r.status_code == 400


def test_convert_rejects_invalid_magic(client):
    r = client.post(
        "/convert",
        files={"file": ("a.pdf", b"not a pdf", "application/pdf")},
    )
    assert r.status_code == 400


def test_convert_rejects_oversize(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 413


def test_convert_rejects_bad_ocr_scope(client):
    r = client.post(
        "/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"ocr_scope": "bogus"},
    )
    assert r.status_code == 400
