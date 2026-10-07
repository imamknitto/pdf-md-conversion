from __future__ import annotations

import io
import threading

_lock = threading.Lock()
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                _engine = RapidOCR()
    return _engine


def ocr_image(image_bytes: bytes) -> str:
    """Run OCR on encoded image bytes and return recognized text (line-joined)."""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    array = np.array(image)

    result, _ = _get_engine()(array)
    if not result:
        return ""
    lines = [line[1] for line in result if len(line) > 1 and line[1]]
    return "\n".join(" ".join(line.split()) for line in lines)
