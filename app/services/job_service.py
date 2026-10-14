"""SQLite-backed registry for async conversion jobs.

The converter runs single-worker (OCR singleton), so a large PDF blocks the
event loop for minutes. Async jobs let the client poll instead: the upload
returns 202 immediately and the heavy `convert_pdf` runs in a thread via
`asyncio.to_thread`, reporting per-page progress into the job record.

Single-process only (same constraint as the OCR engine). State is persisted
to SQLite so jobs survive restarts. Jobs older than CONVERT_JOB_TTL_MINUTES
are swept on access and via periodic cleanup.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..core import config
from ..storage import storage

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

_DB_PATH: Path | None = None
_init_lock = threading.Lock()

# Graceful shutdown state
_shutdown_event = threading.Event()
_shutdown_lock = threading.Lock()

# Cancellation tracking for cooperative cancel
_cancelled_jobs: set[str] = set()
_cancelled_lock = threading.Lock()
_in_progress_jobs: set[str] = set()


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = config.PUBLIC_DIR / "jobs.sqlite"
    return _DB_PATH


def _init_db() -> None:
    """Initialize SQLite schema if not exists."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                options TEXT NOT NULL,
                status TEXT NOT NULL,
                pages_done INTEGER DEFAULT 0,
                pages_total INTEGER,
                result TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
        conn.commit()


def _sweep_expired(conn: sqlite3.Connection) -> None:
    """Delete jobs older than TTL."""
    ttl = config.CONVERT_JOB_TTL_MINUTES * 60
    now = time.time()
    conn.execute(
        "DELETE FROM jobs WHERE ? - created_at > ?",
        (now, ttl),
    )


def new_job_id() -> str:
    return uuid.uuid4().hex


def unfinished_count() -> int:
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        _sweep_expired(conn)
        cur = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN (?, ?)",
            (QUEUED, PROCESSING),
        )
        return cur.fetchone()[0]


def is_shutting_down() -> bool:
    """Check if graceful shutdown has been initiated."""
    return _shutdown_event.is_set()


def initiate_shutdown() -> None:
    """Signal that graceful shutdown has started. New jobs will be rejected."""
    _shutdown_event.set()


def wait_for_jobs(timeout: float = 300.0) -> bool:
    """Wait for all in-progress jobs to complete.

    Returns True if all jobs finished, False if timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _shutdown_lock:
            if not _in_progress_jobs:
                return True
        time.sleep(0.5)
    return False


def _reset_shutdown_state() -> None:
    """Reset shutdown state (for testing only)."""
    _shutdown_event.clear()
    with _shutdown_lock:
        _in_progress_jobs.clear()


def _register_in_progress(job_id: str) -> None:
    with _shutdown_lock:
        _in_progress_jobs.add(job_id)


def _unregister_in_progress(job_id: str) -> None:
    with _shutdown_lock:
        _in_progress_jobs.discard(job_id)


def create_job(doc_id: str, filename: str, options: dict) -> dict | None:
    """Register a queued job.

    Returns None when the queue is full, shutting down, or disk over limit.
    """
    if is_shutting_down():
        return None
    if storage.is_disk_over_limit():
        return None

    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        _sweep_expired(conn)
        active = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN (?, ?)",
            (QUEUED, PROCESSING),
        ).fetchone()[0]
        if active >= config.CONVERT_MAX_QUEUE:
            return None

        now = time.time()
        job_id = new_job_id()
        job = {
            "job_id": job_id,
            "doc_id": doc_id,
            "filename": filename,
            "options": json.dumps(options),
            "status": QUEUED,
            "pages_done": 0,
            "pages_total": None,
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            """
            INSERT INTO jobs (job_id, doc_id, filename, options, status,
                              pages_done, pages_total, result, error,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["job_id"],
                job["doc_id"],
                job["filename"],
                job["options"],
                job["status"],
                job["pages_done"],
                job["pages_total"],
                job["result"],
                job["error"],
                job["created_at"],
                job["updated_at"],
            ),
        )
        conn.commit()
        return {**job, "options": options}


def get_job(job_id: str) -> dict | None:
    _init_db()
    with sqlite3.connect(_get_db_path()) as conn:
        _sweep_expired(conn)
        cur = conn.execute(
            "SELECT job_id, doc_id, filename, options, status, pages_done, "
            "pages_total, result, error, created_at, updated_at "
            "FROM jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "doc_id": row[1],
            "filename": row[2],
            "options": json.loads(row[3]),
            "status": row[4],
            "pages_done": row[5],
            "pages_total": row[6],
            "result": json.loads(row[7]) if row[7] else None,
            "error": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }


def update_job(job_id: str, **fields) -> None:
    _init_db()
    if not fields:
        return
    status = fields.get("status")
    if status == PROCESSING:
        _register_in_progress(job_id)
    elif status in (DONE, ERROR):
        _unregister_in_progress(job_id)

    with sqlite3.connect(_get_db_path()) as conn:
        _sweep_expired(conn)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [time.time(), job_id]
        conn.execute(
            f"UPDATE jobs SET {set_clause}, updated_at = ? WHERE job_id = ?",
            values,
        )
        conn.commit()


def set_result(job_id: str, result: dict) -> None:
    update_job(job_id, status=DONE, result=json.dumps(result))
    _unregister_in_progress(job_id)
    _trigger_webhook_if_configured(job_id, DONE)


def set_error(job_id: str, error: str) -> None:
    update_job(job_id, status=ERROR, error=error)
    _unregister_in_progress(job_id)
    _trigger_webhook_if_configured(job_id, ERROR)


def _trigger_webhook_if_configured(job_id: str, status: str) -> None:
    """Trigger webhook if webhook_url is in job options."""
    job = get_job(job_id)
    if not job:
        return
    options = job.get("options", {})
    webhook_url = options.get("webhook_url")
    if webhook_url:
        trigger_webhook(job_id, webhook_url, status)


def send_webhook(webhook_url: str, payload: dict, max_retries: int = 3) -> bool:
    """Send webhook callback with retries.

    Returns True if successful, False otherwise.
    """
    import httpx

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(webhook_url, json=payload)
                response.raise_for_status()
                return True
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # exponential backoff
    return False


def trigger_webhook(job_id: str, webhook_url: str, status: str) -> None:
    """Trigger webhook callback for job completion.

    Called from set_result/set_error after DB update.
    """
    job = get_job(job_id)
    if not job:
        return

    payload = {
        "job_id": job_id,
        "doc_id": job.get("doc_id"),
        "filename": job.get("filename"),
        "status": status,
        "progress": {
            "pages_done": job.get("pages_done"),
            "pages_total": job.get("pages_total"),
        },
        "result": job.get("result"),
        "error": job.get("error"),
    }

    success = send_webhook(webhook_url, payload)
    if not success:
        import logging
        logger = logging.getLogger("pdf-md-conversion.webhook")
        logger.warning("Webhook delivery failed after retries job=%s url=%s", job_id, webhook_url)


def is_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled (for cooperative cancellation)."""
    with _cancelled_lock:
        return job_id in _cancelled_jobs


def cancel_job(job_id: str) -> bool:
    """Cancel a job and clean up its artifacts.

    Returns True if job was found and cancelled, False if not found or already terminal.
    """
    _init_db()
    job = get_job(job_id)
    if not job:
        return False

    # Only cancel if not already in terminal state
    if job["status"] in (DONE, ERROR, CANCELLED):
        return False

    doc_id = job["doc_id"]

    # Mark as cancelled in tracking set (for worker to check)
    with _cancelled_lock:
        _cancelled_jobs.add(job_id)

    # Update DB status
    update_job(job_id, status=CANCELLED, error="Dibatalkan oleh user")

    # Clean up document artifacts
    try:
        storage.remove_doc(doc_id)
    except Exception:
        pass

    # Unregister from in-progress tracking
    _unregister_in_progress(job_id)

    return True


def clear_cancelled_flag(job_id: str) -> None:
    """Clear cancelled flag (for testing/reset)."""
    with _cancelled_lock:
        _cancelled_jobs.discard(job_id)
