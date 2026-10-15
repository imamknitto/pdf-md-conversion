from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .api.routes import convert, documents, health, jobs, ocr
from .core import config
from .services import job_service as job_svc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pdf-md-conversion")

_SHUTDOWN_TIMEOUT = 300.0  # 5 minutes max wait for in-progress jobs


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


limiter = Limiter(key_func=get_remote_address, default_limits=[])


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
        # Graceful shutdown: stop accepting new jobs, wait for in-progress
        logger.info("Shutdown initiated: stopping job acceptance")
        job_svc.initiate_shutdown()

        logger.info("Waiting for in-progress jobs to complete (timeout=%.1fs)", _SHUTDOWN_TIMEOUT)
        finished = await asyncio.to_thread(job_svc.wait_for_jobs, _SHUTDOWN_TIMEOUT)
        if finished:
            logger.info("All in-progress jobs completed")
        else:
            logger.warning("Shutdown timeout: some jobs may still be running")


app = FastAPI(title="pdf-md-conversion", version="0.1.0", lifespan=lifespan)

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

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

API_PREFIX = "/v1"
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(convert.router, prefix=API_PREFIX)
app.include_router(ocr.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
