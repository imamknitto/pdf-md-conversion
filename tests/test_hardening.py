from __future__ import annotations

import asyncio
import io
import json
import threading
import time

import pymupdf
import pytest
from PIL import Image

from app.api.routes import jobs as jobs_route
from app.core import config
from app.core.errors import JobCancelled
from app.services import job_service as jobs
from app.services.conversion_service import PageContext, process_images
from tests.conftest import sample_bytes


def _job_options(**overrides) -> dict:
    options = {
        "scan_images": False,
        "extract_images": False,
        "ocr_images": False,
        "ocr_scope": "all",
    }
    options.update(overrides)
    return options


# ---------------------------------------------------------------------------
# 0.1 Job lifecycle
# ---------------------------------------------------------------------------


def test_unexpected_error_marks_job_error(client, monkeypatch, tmp_path):
    job = jobs.create_job("a" * 32, "x.pdf", _job_options())
    assert job is not None
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_route, "convert_pdf", boom)
    jobs_route._execute_job(
        jobs_route._JobWork(
            job_id=job["job_id"],
            doc_id="a" * 32,
            tmp_path=pdf,
            filename="x.pdf",
            options=_job_options(),
        )
    )

    stored = jobs.get_job(job["job_id"])
    assert stored is not None
    assert stored["status"] == jobs.ERROR
    assert not pdf.exists()


def test_cancelled_job_stays_cancelled_after_restart_recovery(client):
    job = jobs.create_job("b" * 32, "x.pdf", _job_options())
    assert job is not None
    assert jobs.cancel_job(job["job_id"]) is True

    jobs.recover_interrupted_jobs()

    stored = jobs.get_job(job["job_id"])
    assert stored is not None
    assert stored["status"] == jobs.CANCELLED


def test_recovery_marks_queued_and_processing_as_error(client):
    queued = jobs.create_job("c" * 32, "a.pdf", _job_options())
    processing = jobs.create_job("d" * 32, "b.pdf", _job_options())
    assert queued is not None and processing is not None
    jobs.update_job(processing["job_id"], status=jobs.PROCESSING)

    recovered = jobs.recover_interrupted_jobs()

    assert recovered == 2
    for job in (queued, processing):
        stored = jobs.get_job(job["job_id"])
        assert stored is not None
        assert stored["status"] == jobs.ERROR
        assert "restart" in stored["error"].lower()


def test_cancel_job_is_idempotent(client):
    job = jobs.create_job("e" * 32, "x.pdf", _job_options())
    assert job is not None
    assert jobs.cancel_job(job["job_id"]) is True
    assert jobs.cancel_job(job["job_id"]) is True


def test_cancelled_conversion_does_not_set_error(client, monkeypatch, tmp_path):
    job = jobs.create_job("f" * 32, "x.pdf", _job_options())
    assert job is not None
    assert jobs.cancel_job(job["job_id"]) is True
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def cancelled(*args, **kwargs):
        raise JobCancelled("Job cancelled")

    monkeypatch.setattr(jobs_route, "convert_pdf", cancelled)
    jobs_route._execute_job(
        jobs_route._JobWork(
            job_id=job["job_id"],
            doc_id="f" * 32,
            tmp_path=pdf,
            filename="x.pdf",
            options=_job_options(),
        )
    )

    stored = jobs.get_job(job["job_id"])
    assert stored is not None
    assert stored["status"] == jobs.CANCELLED
    assert not pdf.exists()


def test_worker_starts_and_stops_cleanly():
    jobs_route.start_worker()
    assert jobs_route._worker_thread is not None
    assert jobs_route.stop_worker(5.0) is True


def test_shutdown_rejects_new_jobs(client):
    jobs.initiate_shutdown()
    assert jobs.create_job("a" * 32, "x.pdf", _job_options()) is None


def test_worker_processes_jobs_one_at_a_time(client, monkeypatch):
    from app.services.conversion_service import ConvertResult

    started = threading.Event()
    release = threading.Event()

    def slow(**kwargs):
        started.set()
        release.wait(5.0)
        return ConvertResult(doc_id=kwargs["doc_id"], markdown="## Page 1\n", pages=1)

    monkeypatch.setattr(jobs_route, "convert_pdf", slow)
    first = client.post(
        "/v1/convert/jobs",
        files={"file": ("a.pdf", sample_bytes(), "application/pdf")},
    )
    second = client.post(
        "/v1/convert/jobs",
        files={"file": ("b.pdf", sample_bytes(), "application/pdf")},
    )
    assert first.status_code == 202 and second.status_code == 202
    try:
        assert started.wait(5.0)
        second_job = jobs.get_job(second.json()["job_id"])
        assert second_job is not None
        assert second_job["status"] == jobs.QUEUED
    finally:
        release.set()

    deadline = time.time() + 10.0
    while time.time() < deadline:
        statuses = {
            jobs.get_job(first.json()["job_id"])["status"],
            jobs.get_job(second.json()["job_id"])["status"],
        }
        if statuses == {jobs.DONE}:
            break
        time.sleep(0.05)
    assert statuses == {jobs.DONE}


def test_concurrent_polling_has_no_lock_errors(client):
    job = jobs.create_job("a" * 32, "x.pdf", _job_options())
    assert job is not None
    errors: list[Exception] = []

    def poll() -> None:
        try:
            for _ in range(100):
                jobs.get_job(job["job_id"])
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_expired_jobs_are_swept(client, monkeypatch):
    job = jobs.create_job("a" * 32, "x.pdf", _job_options())
    assert job is not None
    monkeypatch.setattr(config, "CONVERT_JOB_TTL_MINUTES", -1)
    jobs._last_sweep = 0.0
    assert jobs.get_job(job["job_id"]) is None


# ---------------------------------------------------------------------------
# 0.2 Webhook
# ---------------------------------------------------------------------------


def _public_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/hook",
        "ftp://example.com/hook",
        "http://user:pass@example.com/hook",
        "http://example.com:8080/hook",
    ],
)
def test_webhook_url_rejects_unsafe_targets(url):
    with pytest.raises(jobs.WebhookUrlError):
        jobs.validate_webhook_url(url)


def test_webhook_url_allows_public_host(monkeypatch):
    monkeypatch.setattr(jobs.socket, "getaddrinfo", _public_dns)
    jobs.validate_webhook_url("https://example.com/hook")


def test_submit_rejects_internal_webhook(client):
    r = client.post(
        "/v1/convert/jobs",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"webhook_url": "http://127.0.0.1/hook"},
    )
    assert r.status_code == 400


def test_webhook_success_does_not_change_job_status(client, monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(jobs, "validate_webhook_url", lambda url: None)
    monkeypatch.setattr(
        jobs,
        "send_webhook",
        lambda url, payload: calls.append((url, payload["status"])) or True,
    )

    r = client.post(
        "/v1/convert/jobs",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"webhook_url": "http://example.com/hook"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    deadline = time.time() + 10.0
    while time.time() < deadline and not calls:
        time.sleep(0.05)

    stored = jobs.get_job(job_id)
    assert stored is not None
    assert stored["status"] == jobs.DONE
    assert calls == [("http://example.com/hook", jobs.DONE)]


def test_send_webhook_success_with_mocked_httpx(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json):
            return _Response()

    monkeypatch.setattr(jobs.httpx, "Client", _Client)
    assert jobs.send_webhook("https://example.com/hook", {"ok": True}) is True


def test_send_webhook_gives_up_after_retries(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(jobs.httpx, "Client", _Client)
    assert jobs.send_webhook("https://example.com/hook", {}, max_retries=1) is False


def test_webhook_failure_does_not_change_job_status(client, monkeypatch):
    monkeypatch.setattr(jobs, "validate_webhook_url", lambda url: None)
    monkeypatch.setattr(jobs, "send_webhook", lambda url, payload: False)

    r = client.post(
        "/v1/convert/jobs",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"webhook_url": "http://example.com/hook"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    deadline = time.time() + 10.0
    stored = None
    while time.time() < deadline:
        stored = jobs.get_job(job_id)
        if stored is not None and stored["status"] in (jobs.DONE, jobs.ERROR):
            break
        time.sleep(0.05)
    assert stored is not None
    assert stored["status"] == jobs.DONE


# ---------------------------------------------------------------------------
# 0.3 Resource limits / output
# ---------------------------------------------------------------------------


class _FakeParent:
    @staticmethod
    def extract_image(xref):
        return {"image": b"x" * 16, "ext": "png", "width": 10, "height": 10}


class _FakePage:
    parent = _FakeParent()


def _fake_ctx(image_infos) -> PageContext:
    return PageContext(
        number=1,
        page=_FakePage(),
        plumber_page=None,
        raw_text="",
        is_scan=False,
        has_cover_image=False,
        tables=[],
        exclude_rects=[],
        recurring_xrefs=set(),
        image_infos=image_infos,
    )


def test_image_cap_is_enforced_per_document(monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGES_PER_DOC", 1)
    ctx = _fake_ctx([(1, 0.5), (2, 0.5)])

    first, *_rest = process_images(
        ctx, "a" * 32, True, False, "all", 0, 0, 0, False, 0, images_kept_so_far=0
    )
    assert first.images_kept == 1
    assert first.images_skipped == 1
    assert sum("Batas 1 gambar" in w for w in first.warnings) == 1

    second, *_rest = process_images(
        ctx, "a" * 32, True, False, "all", 0, 0, 0, True, 0, images_kept_so_far=1
    )
    assert second.images_kept == 0
    assert second.images_skipped == 2
    assert not any("Batas 1 gambar" in w for w in second.warnings)


def test_oversized_embedded_image_skipped(monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 10)
    ctx = _fake_ctx([(1, 0.5)])

    result, *_rest = process_images(
        ctx, "a" * 32, True, False, "all", 0, 0, 0, False, 0, images_kept_so_far=0
    )
    assert result.images_kept == 0
    assert result.images_skipped == 1
    assert any("piksel" in w for w in result.warnings)


def test_pdf_page_limit(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_PAGES", 0)
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
    )
    assert r.status_code == 400


def test_encrypted_pdf_rejected(client):
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    doc.close()

    r = client.post("/v1/convert", files={"file": ("secret.pdf", data, "application/pdf")})
    assert r.status_code == 400


def _png_bytes(width: int = 200, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_image_pixel_limit(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 10)
    r = client.post("/v1/ocr", files={"file": ("scan.png", _png_bytes(), "image/png")})
    assert r.status_code == 413


def test_no_base64_when_images_not_requested(client):
    r = client.post(
        "/v1/convert",
        files={"file": ("flowchart-pdf.pdf", sample_bytes(), "application/pdf")},
        data={"extract_images": "false", "scan_images": "false"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["images"] == []
    assert "data_base64" not in r.text


# ---------------------------------------------------------------------------
# 0.4 Error contract
# ---------------------------------------------------------------------------


def test_rate_limit_handler_uses_detail_shape():
    from app.main import _rate_limit_exceeded_handler

    class _Exc:
        detail = "10 per 1 minute"

    response = asyncio.run(_rate_limit_exceeded_handler(None, _Exc()))
    assert response.status_code == 429
    assert json.loads(response.body) == {"detail": "Rate limit terlampaui: 10 per 1 minute"}
