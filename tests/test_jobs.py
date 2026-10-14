from __future__ import annotations

import time

from tests.conftest import sample_bytes


def _poll_done(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/convert/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.5)
    raise AssertionError("job tidak selesai dalam batas waktu")


def test_job_submit_and_poll(client):
    r = client.post(
        "/v1/convert/jobs",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 202
    accepted = r.json()
    assert accepted["status"] == "queued"
    assert accepted["job_id"] and accepted["doc_id"]

    done = _poll_done(client, accepted["job_id"])
    assert done["status"] == "done"
    assert done["result"]["doc_id"] == accepted["doc_id"]
    assert done["result"]["markdown"].startswith("## Page 1")


def test_job_unknown_id_404(client):
    r = client.get("/v1/convert/jobs/" + "a" * 32)
    assert r.status_code == 404


def test_job_invalid_id_format_404(client):
    r = client.get("/v1/convert/jobs/bukan-id")
    assert r.status_code == 404


def test_job_queue_full_429(client, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "CONVERT_MAX_QUEUE", 0)
    r = client.post(
        "/v1/convert/jobs",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 429
