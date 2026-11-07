from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pdf-md-conversion")
except PackageNotFoundError:  # pragma: no cover - running from a plain checkout
    __version__ = "0.1.0"
