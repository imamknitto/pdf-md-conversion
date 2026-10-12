from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import convert, documents, health, jobs
from .core import config
from .storage.cleanup import cleanup_loop, sweep_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("doc-md-conversion")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    sweep_once()
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="doc-md-conversion", version="0.1.0", lifespan=lifespan)


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "PLATFORM_URL",
        "http://127.0.0.1:8378,http://localhost:8378,http://127.0.0.1:8377,http://localhost:8377",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

config.PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/public", StaticFiles(directory=str(config.PUBLIC_DIR)), name="public")

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(convert.router)
app.include_router(jobs.router)
