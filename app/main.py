from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from .api.routes import convert, health, jobs, ocr
from .core import config
from .core.rate_limit import limiter
from .core.version import __version__
from .services import job_service as job_svc

logger = logging.getLogger("pdf-md-conversion")

_SHUTDOWN_TIMEOUT = 300.0  # 5 minutes max wait for in-progress jobs


def _configure_logging() -> None:
    """Configure console logging at startup instead of import time."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


_URL_RE = re.compile(
    r"^https?://"  # scheme
    r"(?:[a-zA-Z0-9-]+\.)*[a-zA-Z0-9-]+"  # host
    r"(?::\d+)?"  # optional port
    r"(?:/.*)?$"  # optional path
)


def _validate_cors_origins(raw: str) -> list[str]:
    """Parse and validate CORS origins from PLATFORM_URL env var.

    Raises ValueError on malformed entry to fail fast at startup.
    """
    origins = []
    for part in raw.split(","):
        origin = part.strip()
        if not origin:
            continue
        if not _URL_RE.match(origin):
            raise ValueError(f"Invalid CORS origin in PLATFORM_URL: {origin!r}")
        origins.append(origin)
    return origins


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "PLATFORM_URL",
        "http://127.0.0.1:8378,http://localhost:8378,http://127.0.0.1:8377,http://localhost:8377",
    )
    return _validate_cors_origins(raw)


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 in the same `{"detail": ...}` shape as every other API error."""
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit terlampaui: {exc.detail}"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    recovered = await asyncio.to_thread(job_svc.recover_interrupted_jobs)
    if recovered:
        logger.warning("Recovered %d interrupted job(s) as error", recovered)
    jobs.start_worker()
    try:
        yield
    finally:
        # Graceful shutdown: stop accepting new jobs, finish the active job.
        logger.info("Shutdown initiated: stopping job acceptance")
        job_svc.initiate_shutdown()

        logger.info("Waiting for the active job to complete (timeout=%.1fs)", _SHUTDOWN_TIMEOUT)
        finished = await asyncio.to_thread(jobs.stop_worker, _SHUTDOWN_TIMEOUT)
        if finished:
            logger.info("Conversion worker stopped cleanly")
        else:
            logger.warning("Shutdown timeout: a job may still be running")


app = FastAPI(title="pdf-md-conversion", version=__version__, lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.middleware("http")
async def version_header_middleware(request: Request, call_next):
    """Add API version header to all responses."""
    response: Response = await call_next(request)
    response.headers["X-API-Version"] = "v1"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress large responses (markdown + base64 images). Small bodies are below
# the threshold, so error/health payloads are unaffected.
app.add_middleware(GZipMiddleware, minimum_size=1024)

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

API_PREFIX = "/v1"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(convert.router, prefix=API_PREFIX)
app.include_router(ocr.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
