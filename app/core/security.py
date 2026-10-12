from __future__ import annotations

import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def _require_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Bearer auth, active only when CONVERTER_API_KEY is set."""
    expected = os.getenv("CONVERTER_API_KEY", "")
    if not expected:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Unauthorized.")
