from __future__ import annotations

import io
import threading

from ..core import config

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


def upscale_safe(image):
    """Perbesar gambar kecil secara aman sebelum OCR (LANCZOS, ber-batas).

    Hanya memperbesar bila sisi terpanjang < OCR_MIN_SIDE, maksimal
    OCR_MAX_SCALE, dan total piksel tidak melewati OCR_MAX_PIXELS.
    Gagal resize -> kembalikan gambar asli (tidak pernah raise).
    """
    try:
        w, h = image.size
        longest = max(w, h)
        if longest <= 0 or longest >= config.OCR_MIN_SIDE:
            return image
        scale = min(config.OCR_MAX_SCALE, config.OCR_MIN_SIDE / longest)
        if scale <= 1.01:
            return image
        while int(w * scale) * int(h * scale) > config.OCR_MAX_PIXELS and scale > 1.01:
            scale = max(1.01, scale * 0.9)
            if scale <= 1.011:
                return image
        from PIL import Image as _Image

        return image.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)
    except Exception:
        return image


def _box_center(box) -> tuple[float, float]:
    try:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    except Exception:
        return (0.0, 0.0)


def sort_boxes_top_left(boxes: list[dict], y_tol: float | None = None) -> list[dict]:
    """Urutkan box OCR top-to-bottom, left-to-right dengan toleransi baris."""
    tol = config.FLOWCHART_Y_TOL if y_tol is None else y_tol
    items = sorted(boxes, key=lambda b: (b.get("cy", 0.0), b.get("cx", 0.0)))
    rows: list[list[dict]] = []
    for b in items:
        placed = False
        for row in rows:
            if abs(b.get("cy", 0.0) - row[0].get("cy", 0.0)) <= tol:
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])
    rows.sort(key=lambda r: min(x.get("cy", 0.0) for x in r))
    ordered: list[dict] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda b: b.get("cx", 0.0)))
    return ordered


def prepare_image(image_bytes: bytes) -> tuple:
    """Decode + fit an image for OCR.

    Returns (image_or_None, status) with status one of:
    'ok' | 'tiny' (below OCR_SKIP_BELOW, likely an icon) | 'broken'.
    Large images are downscaled to OCR_MAX_SIDE; small ones go through
    upscale_safe as before.
    """
    from PIL import Image as _Image

    try:
        image = _Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None, "broken"
    try:
        w, h = image.size
        longest = max(w, h)
        if longest <= 0:
            return None, "broken"
        if longest < config.OCR_SKIP_BELOW:
            return None, "tiny"
        if longest > config.OCR_MAX_SIDE:
            scale = config.OCR_MAX_SIDE / longest
            image = image.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                _Image.LANCZOS,
            )
    except Exception:
        return None, "broken"
    return upscale_safe(image), "ok"


def ocr_image_boxes(image_bytes: bytes) -> list[dict]:
    """OCR dan kembalikan box berposisi: [{text, cx, cy, conf}]."""
    boxes, _ = ocr_image_boxes_ex(image_bytes)
    return boxes


def ocr_image_boxes_ex(image_bytes: bytes) -> tuple[list[dict], str]:
    """Like ocr_image_boxes but also reports why nothing came back.

    status: 'ok' | 'tiny' | 'broken' | 'empty' (no text found).
    """
    import numpy as np

    image, pre = prepare_image(image_bytes)
    if image is None:
        return [], pre
    try:
        array = np.array(image)
        result, _ = _get_engine()(array)
    except Exception:
        return [], "broken"
    if not result:
        return [], "empty"
    boxes: list[dict] = []
    for line in result:
        try:
            text = " ".join(str(line[1]).split())
            if not text:
                continue
            cx, cy = _box_center(line[0])
            conf = float(line[2]) if len(line) > 2 else 0.0
            boxes.append({"text": text, "cx": cx, "cy": cy, "conf": conf})
        except Exception:
            continue
    return sort_boxes_top_left(boxes), "ok"


def ocr_image(image_bytes: bytes) -> str:
    """Run OCR on encoded image bytes and return recognized text (line-joined).

    Kompatibel dengan pemanggil lama; internal kini memakai upscale aman
    dan pengurutan top-to-bottom / left-to-right.
    """
    boxes = ocr_image_boxes(image_bytes)
    return "\n".join(b["text"] for b in boxes)
