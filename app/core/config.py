from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_BOOL_TRUE = {"1", "true", "yes", "on"}

_INT_FIELDS = {
    "MAX_UPLOAD_MB",
    "PUBLIC_TTL_MINUTES",
    "PUBLIC_MAX_MB",
    "CLEANUP_INTERVAL_MINUTES",
    "MIN_TEXT_CHARS",
    "MAX_IMAGE_MB",
    "MAX_IMAGES_PER_DOC",
    "OCR_DPI",
    "OCR_MIN_SIDE",
    "OCR_MAX_PIXELS",
    "OCR_MAX_SIDE",
    "OCR_SKIP_BELOW",
    "OCR_MAX_IMAGES",
    "FLOWCHART_MAX_SHAPES",
    "FLOWCHART_SCORE_MIN",
    "CONVERT_JOB_TTL_MINUTES",
    "CONVERT_MAX_QUEUE",
}

_FLOAT_FIELDS = {
    "PAGE_COVERAGE",
    "RECURRING_XREF_RATIO",
    "IMAGE_MIN_AREA_RATIO",
    "OCR_MAX_SCALE",
    "FLOWCHART_Y_TOL",
}

_DEFAULTS: dict[str, Any] = {
    "MAX_UPLOAD_MB": 30,
    "PUBLIC_TTL_MINUTES": 60,
    "PUBLIC_MAX_MB": 1024,
    "CLEANUP_INTERVAL_MINUTES": 10,
    "MIN_TEXT_CHARS": 50,
    "PAGE_COVERAGE": 0.5,
    "RECURRING_XREF_RATIO": 0.5,
    "IMAGE_MIN_AREA_RATIO": 0.01,
    "MAX_IMAGE_MB": 10,
    "MAX_IMAGES_PER_DOC": 200,
    "OCR_DPI": 200,
    "OCR_MIN_SIDE": 1200,
    "OCR_MAX_SCALE": 2.0,
    "OCR_MAX_PIXELS": 9000000,
    "OCR_MAX_SIDE": 1600,
    "OCR_SKIP_BELOW": 100,
    "OCR_MAX_IMAGES": 100,
    "FLOWCHART_ENABLE": True,
    "FLOWCHART_Y_TOL": 10.0,
    "FLOWCHART_MAX_SHAPES": 200,
    "FLOWCHART_SCORE_MIN": 2,
    "CONVERT_JOB_TTL_MINUTES": 120,
    "CONVERT_MAX_QUEUE": 4,
}


class Settings(BaseSettings):
    """Knobs aplikasi; nama env var, default, dan tipe sama seperti tabel README.

    Nilai env yang tidak valid jatuh kembali ke default (perilaku config lama).
    CONVERTER_API_KEY + PLATFORM_URL sengaja tidak di sini: dibaca per-request
    via os.getenv agar perubahan env/auth test tetap berlaku saat runtime.
    """

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    MAX_UPLOAD_MB: int = 30
    PUBLIC_DIR: Path = BASE_DIR / "public"
    PUBLIC_TTL_MINUTES: int = 60
    PUBLIC_MAX_MB: int = 1024
    CLEANUP_INTERVAL_MINUTES: int = 10
    MIN_TEXT_CHARS: int = 50
    PAGE_COVERAGE: float = 0.5
    RECURRING_XREF_RATIO: float = 0.5
    IMAGE_MIN_AREA_RATIO: float = 0.01
    MAX_IMAGE_MB: int = 10
    MAX_IMAGES_PER_DOC: int = 200
    OCR_DPI: int = 200
    OCR_MIN_SIDE: int = 1200
    OCR_MAX_SCALE: float = 2.0
    OCR_MAX_PIXELS: int = 9000000
    OCR_MAX_SIDE: int = 1600
    OCR_SKIP_BELOW: int = 100
    OCR_MAX_IMAGES: int = 100
    FLOWCHART_ENABLE: bool = True
    FLOWCHART_Y_TOL: float = 10.0
    FLOWCHART_MAX_SHAPES: int = 200
    FLOWCHART_SCORE_MIN: int = 2
    CONVERT_JOB_TTL_MINUTES: int = 120
    CONVERT_MAX_QUEUE: int = 4

    @field_validator(*_INT_FIELDS, mode="before")
    @classmethod
    def _lenient_int(cls, v: Any, info: ValidationInfo) -> Any:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return _DEFAULTS[info.field_name]
        try:
            return int(v)
        except (TypeError, ValueError):
            return _DEFAULTS[info.field_name]

    @field_validator(*_FLOAT_FIELDS, mode="before")
    @classmethod
    def _lenient_float(cls, v: Any, info: ValidationInfo) -> Any:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return _DEFAULTS[info.field_name]
        try:
            return float(v)
        except (TypeError, ValueError):
            return _DEFAULTS[info.field_name]

    @field_validator("FLOWCHART_ENABLE", mode="before")
    @classmethod
    def _lenient_bool(cls, v: Any) -> Any:
        if v is None:
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in _BOOL_TRUE

    @field_validator("PUBLIC_DIR", mode="before")
    @classmethod
    def _lenient_path(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return BASE_DIR / "public"
        return v

    @property
    def MAX_UPLOAD_BYTES(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def PUBLIC_MAX_BYTES(self) -> int:
        return self.PUBLIC_MAX_MB * 1024 * 1024

    @property
    def MAX_IMAGE_BYTES(self) -> int:
        return self.MAX_IMAGE_MB * 1024 * 1024


settings = Settings()


def __getattr__(name: str) -> Any:
    # Kompatibilitas akses modul gaya lama: config.MAX_UPLOAD_MB, ...
    # (monkeypatch/setattr pada modul tetap berfungsi karena mengisi __dict__ modul).
    try:
        return getattr(settings, name)
    except AttributeError:
        raise AttributeError(f"module 'config' has no attribute {name!r}")
