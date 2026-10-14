from __future__ import annotations

import asyncio
import logging
import shutil
import time

from ..core import config

logger = logging.getLogger("pdf-md-conversion.cleanup")


def _dir_size(path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def sweep_once(now: float | None = None) -> dict:
    base = config.PUBLIC_DIR
    stats = {"deleted_expired": 0, "deleted_oversize": 0}
    if not base.exists():
        return stats

    now = time.time() if now is None else now
    ttl = config.PUBLIC_TTL_MINUTES * 60

    dirs = [d for d in base.iterdir() if d.is_dir()]
    for d in dirs:
        try:
            if now - d.stat().st_mtime > ttl:
                shutil.rmtree(d, ignore_errors=True)
                stats["deleted_expired"] += 1
        except OSError:
            continue

    dirs = [d for d in base.iterdir() if d.is_dir()]
    sized = []
    total = 0
    for d in dirs:
        size = _dir_size(d)
        sized.append((d, size))
        total += size

    if total > config.PUBLIC_MAX_BYTES:
        for d, size in sorted(sized, key=lambda pair: pair[0].stat().st_mtime):
            if total <= config.PUBLIC_MAX_BYTES:
                break
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            stats["deleted_oversize"] += 1

    return stats


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(config.CLEANUP_INTERVAL_MINUTES * 60)
        try:
            stats = sweep_once()
            if stats["deleted_expired"] or stats["deleted_oversize"]:
                logger.info("cleanup: %s", stats)
        except Exception:
            logger.exception("cleanup sweep failed")
