"""In-memory registry for async conversion jobs.

The converter runs single-worker (OCR singleton), so a large PDF blocks the
event loop for minutes. Async jobs let the client poll instead: the upload
returns 202 immediately and the heavy `convert_pdf` runs in a thread via
`asyncio.to_thread`, reporting per-page progress into the job record.

Single-process only (same constraint as the OCR engine). State is best
effort: jobs older than CONVERT_JOB_TTL_MINUTES are swept lazily.
"""

from __future__ import annotations

import threading
import time
import uuid

from ..core import config

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def new_job_id() -> str:
    return uuid.uuid4().hex


def _sweep_locked() -> None:
    ttl = config.CONVERT_JOB_TTL_MINUTES * 60
    now = time.time()
    stale = [jid for jid, j in _jobs.items() if now - j.get("created_at", now) > ttl]
    for jid in stale:
        del _jobs[jid]


def unfinished_count() -> int:
    with _lock:
        _sweep_locked()
        return sum(1 for j in _jobs.values() if j["status"] in (QUEUED, PROCESSING))


def create_job(doc_id: str, filename: str, options: dict) -> dict | None:
    """Register a queued job. Returns None when the queue is full."""
    with _lock:
        _sweep_locked()
        active = sum(1 for j in _jobs.values() if j["status"] in (QUEUED, PROCESSING))
        if active >= config.CONVERT_MAX_QUEUE:
            return None
        now = time.time()
        job_id = new_job_id()
        job = {
            "job_id": job_id,
            "doc_id": doc_id,
            "filename": filename,
            "options": options,
            "status": QUEUED,
            "pages_done": 0,
            "pages_total": None,
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        _jobs[job_id] = job
        return dict(job)


def get_job(job_id: str) -> dict | None:
    with _lock:
        _sweep_locked()
        job = _jobs.get(job_id)
        return dict(job) if job else None


def update_job(job_id: str, **fields) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def set_result(job_id: str, result: dict) -> None:
    update_job(job_id, status=DONE, result=result)


def set_error(job_id: str, error: str) -> None:
    update_job(job_id, status=ERROR, error=error)
