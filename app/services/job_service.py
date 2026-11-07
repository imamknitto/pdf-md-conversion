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

import ipaddress
import json
import logging
import socket
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..core import config
from ..storage import storage

logger = logging.getLogger("pdf-md-conversion.webhook")

_HTTP_PORTS = {"http": 80, "https": 443}
_WEBHOOK_ATTEMPTS = 3

QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

_DB_PATH: Path | None = None
_initialized_paths: set[Path] = set()
_init_lock = threading.Lock()

_SWEEP_INTERVAL_SECONDS = 60.0
_last_sweep = 0.0
_sweep_lock = threading.Lock()

# Graceful shutdown state
_shutdown_event = threading.Event()

# Cancellation tracking for cooperative cancel
_cancelled_jobs: set[str] = set()
_cancelled_lock = threading.Lock()


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = config.DATA_DIR / "jobs.sqlite"
    return _DB_PATH


@contextmanager
def _connect():
    """Open a SQLite connection with busy timeout for concurrent polling."""
    conn = sqlite3.connect(_get_db_path(), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    """Initialize SQLite schema once per database path."""
    db_path = _get_db_path()
    if db_path in _initialized_paths:
        return
    with _init_lock:
        if db_path in _initialized_paths:
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)

        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
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
        _initialized_paths.add(db_path)


def _sweep_expired(conn: sqlite3.Connection) -> None:
    """Delete jobs older than TTL."""
    ttl = config.CONVERT_JOB_TTL_MINUTES * 60
    now = time.time()
    conn.execute(
        "DELETE FROM jobs WHERE ? - created_at > ?",
        (now, ttl),
    )


def _maybe_sweep(conn: sqlite3.Connection) -> None:
    """Sweep expired jobs at most once per minute instead of on every access."""
    global _last_sweep
    now = time.time()
    with _sweep_lock:
        if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep = now
    _sweep_expired(conn)


def new_job_id() -> str:
    return uuid.uuid4().hex


def recover_interrupted_jobs() -> int:
    """Mark jobs left queued/processing by a previous process as error.

    Called once at startup: a restarted service cannot resume in-flight work,
    and leaving rows in queued/processing would keep them in the queue count
    until TTL. Cancelled/done/error rows are never touched.
    """
    _init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? "
            "WHERE status IN (?, ?)",
            (
                ERROR,
                "Interrupted by service restart",
                time.time(),
                QUEUED,
                PROCESSING,
            ),
        )
        rowcount = cur.rowcount
    return rowcount


def is_shutting_down() -> bool:
    """Check if graceful shutdown has been initiated."""
    return _shutdown_event.is_set()


def initiate_shutdown() -> None:
    """Signal that graceful shutdown has started. New jobs will be rejected."""
    _shutdown_event.set()


def _reset_shutdown_state() -> None:
    """Reset shutdown state (for testing only)."""
    _shutdown_event.clear()


def create_job(doc_id: str, filename: str, options: dict) -> dict | None:
    """Register a queued job.

    Returns None when the queue is full or shutting down.
    """
    if is_shutting_down():
        return None

    _init_db()
    with _connect() as conn:
        _maybe_sweep(conn)
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
        return {**job, "options": options}


def get_job(job_id: str) -> dict | None:
    _init_db()
    with _connect() as conn:
        _maybe_sweep(conn)
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

    with _connect() as conn:
        _maybe_sweep(conn)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [time.time(), job_id]
        conn.execute(
            f"UPDATE jobs SET {set_clause}, updated_at = ? WHERE job_id = ?",
            values,
        )


def set_result(job_id: str, result: dict) -> None:
    update_job(job_id, status=DONE, result=json.dumps(result))


def set_error(job_id: str, error: str) -> None:
    update_job(job_id, status=ERROR, error=error)


class WebhookUrlError(ValueError):
    """Raised when a webhook URL fails validation (syntax or SSRF guard)."""


def validate_webhook_url(url: str) -> None:
    """Validate a webhook URL against the SSRF guard.

    Only public http(s) hosts on standard ports are allowed. Redirects and
    URL credentials are not supported. Raises WebhookUrlError otherwise.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise WebhookUrlError("webhook_url tidak valid.") from None
    if parsed.scheme not in _HTTP_PORTS:
        raise WebhookUrlError("webhook_url harus URL http/https yang valid.")
    host = parsed.hostname
    if not host:
        raise WebhookUrlError("webhook_url harus menyertakan host.")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookUrlError("webhook_url tidak boleh memuat kredensial.")
    try:
        port = parsed.port
    except ValueError:
        raise WebhookUrlError("webhook_url memuat port tidak valid.") from None
    default_port = _HTTP_PORTS[parsed.scheme]
    if port is not None and port != default_port:
        raise WebhookUrlError("webhook_url hanya boleh memakai port standar (80/443).")

    try:
        infos = socket.getaddrinfo(host, port or default_port, type=socket.SOCK_STREAM)
    except OSError:
        raise WebhookUrlError("Host webhook_url tidak dapat di-resolve.") from None
    if not infos:
        raise WebhookUrlError("Host webhook_url tidak dapat di-resolve.")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            raise WebhookUrlError("Host webhook_url tidak valid.") from None
        if not ip.is_global:
            raise WebhookUrlError(
                "webhook_url tidak boleh mengarah ke host privat/internal."
            )


def send_webhook(webhook_url: str, payload: dict, max_retries: int = _WEBHOOK_ATTEMPTS) -> bool:
    """Send webhook callback with bounded retries (blocking; call from a thread)."""
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                response = client.post(webhook_url, json=payload)
                response.raise_for_status()
                return True
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(min(2**attempt, 5))  # exponential backoff, max 5 s
    return False


def deliver_webhook(job_id: str, status: str, job: dict | None = None) -> bool:
    """Deliver the completion callback for a job (blocking; call from a thread).

    Pass ``job`` when the caller already has the record to avoid a second read.
    """
    if job is None:
        job = get_job(job_id)
    if not job:
        return False
    webhook_url = (job.get("options") or {}).get("webhook_url")
    if not webhook_url:
        return False

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

    try:
        # Re-validate at delivery: DNS may have changed since submit.
        validate_webhook_url(webhook_url)
    except WebhookUrlError as exc:
        logger.warning("Webhook rejected job=%s: %s", job_id, exc)
        return False

    if not send_webhook(webhook_url, payload):
        logger.warning("Webhook delivery failed after retries job=%s url=%s", job_id, webhook_url)
        return False
    return True


def is_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled (for cooperative cancellation)."""
    with _cancelled_lock:
        return job_id in _cancelled_jobs


def cancel_job(job_id: str) -> bool:
    """Cancel a job and clean up its artifacts.

    Returns True if job was found and is cancelled (idempotent), False if not
    found or already in a non-cancellable terminal state.
    """
    _init_db()
    job = get_job(job_id)
    if not job:
        return False

    if job["status"] == CANCELLED:
        return True
    if job["status"] in (DONE, ERROR):
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

    return True


def clear_cancelled_flag(job_id: str) -> None:
    """Clear cancelled flag (for testing/reset)."""
    with _cancelled_lock:
        _cancelled_jobs.discard(job_id)
