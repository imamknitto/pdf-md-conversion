from __future__ import annotations

import logging
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

logger = logging.getLogger("pdf-md-conversion.security")


def _require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Bearer auth, active only when PDF_MD_CONVERTER_API_KEY is set."""
    expected = os.getenv("PDF_MD_CONVERTER_API_KEY", "")
    if not expected:
        return
    if credentials is None:
        logger.warning("auth missing: no credentials provided")
        raise HTTPException(status_code=401, detail="Unauthorized.")
    if credentials.credentials != expected:
        # Log only first 4 chars for debugging, never full token
        token = credentials.credentials
        token_preview = token[:4] + "..." if len(token) > 4 else "****"
        logger.warning("auth failed: invalid token prefix=%s", token_preview)
        raise HTTPException(status_code=401, detail="Unauthorized.")
