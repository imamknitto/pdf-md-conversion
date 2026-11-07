from __future__ import annotations

import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def rate_limit_key(request: Request) -> str:
    """Rate limit key: hashed API key if authenticated, else client IP.

    The token is hashed so raw credentials are never kept as in-memory keys.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            return f"apikey:{digest}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_remote_address, default_limits=[], headers_enabled=False)
