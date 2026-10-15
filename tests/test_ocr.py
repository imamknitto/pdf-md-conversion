from __future__ import annotations

import base64
import io
from unittest.mock import patch

from PIL import Image

from app.core import config
from tests.conftest import auth_headers


def _png_bytes(width: int = 200, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_ocr_ok(image_bytes: bytes):
    return (
        [
            {"text": "Hello OCR", "cx": 10.0, "cy": 10.0, "conf": 0.99},
            {"text": "Line two", "cx": 10.0, "cy": 30.0, "conf": 0.95},
        ],
        "ok",
    )


def test_ocr_standalone_structure(client):
    with patch(
        "app.services.conversion_service.ocr_image_boxes_ex",
        side_effect=_mock_ocr_ok,
    ):
        r = client.post(
            "/v1/ocr",
            files={"file": ("scan.png", _png_bytes(), "image/png")},
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
    assert data["stats"]["pages"] == 1
    assert data["stats"]["images_kept"] == 1
    assert data["stats"]["tables"] == 0
    assert data["stats"]["scanned_pages"] == 0
    assert len(data["images"]) == 1
    img = data["images"][0]
    assert img["kind"] == "image"
    assert img["page"] == 1
    assert img["ocr_text"] == "Hello OCR\nLine two"
    assert img["data_base64"]
    assert len(base64.b64decode(img["data_base64"])) == img["size_bytes"]
    assert "Hello OCR" in data["markdown"]
    assert data["markdown_url"] == f"/public/{data['doc_id']}/doc.md"
    assert not (config.DATA_DIR / data["doc_id"]).exists()


def test_ocr_rejects_pdf(client):
    r = client.post(
        "/v1/ocr",
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400


def test_ocr_rejects_txt(client):
    r = client.post(
        "/v1/ocr",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_ocr_rejects_corrupt_image(client):
    r = client.post(
        "/v1/ocr",
        files={"file": ("bad.png", b"not-an-image", "image/png")},
    )
    assert r.status_code == 400


def test_ocr_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_BYTES", 10)
    r = client.post(
        "/v1/ocr",
        files={"file": ("scan.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 413


def test_ocr_requires_auth_when_key_set(authed_client):
    r = authed_client.post(
        "/v1/ocr",
        files={"file": ("scan.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 401

    with patch(
        "app.services.conversion_service.ocr_image_boxes_ex",
        side_effect=_mock_ocr_ok,
    ):
        r2 = authed_client.post(
            "/v1/ocr",
            files={"file": ("scan.png", _png_bytes(), "image/png")},
            headers=auth_headers(),
        )
    assert r2.status_code == 200
