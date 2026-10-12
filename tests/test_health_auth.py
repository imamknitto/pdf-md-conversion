from __future__ import annotations

from tests.conftest import auth_headers


def test_health_open_without_key(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "doc-md-conversion", "version": "0.1.0"}


def test_health_open_with_key_set(authed_client):
    r = authed_client.get("/health")
    assert r.status_code == 200


def test_convert_requires_key_when_set(authed_client):
    r = authed_client.post("/convert", files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 401


def test_convert_ok_with_key(authed_client):
    from tests.conftest import sample_bytes

    r = authed_client.post(
        "/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        headers=auth_headers(),
    )
    assert r.status_code == 200
