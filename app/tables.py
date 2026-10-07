from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

MAX_CELL_CHARS = 300


@dataclass
class TableResult:
    bbox: tuple[float, float, float, float]
    rows: list[list[str]] = field(default_factory=list)
    source: str = ""

    @property
    def score(self) -> int:
        if not self.rows:
            return 0
        return len(self.rows) * max(len(r) for r in self.rows)


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split())


def _normalize(rows) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows or []:
        out.append([_clean_cell(c) for c in row])
    return out


def _valid(rows: list[list[str]], page: pymupdf.Page) -> bool:
    if len(rows) < 2:
        return False
    ncols = max((len(r) for r in rows), default=0)
    if ncols < 2:
        return False
    for row in rows:
        for cell in row:
            if len(cell) > MAX_CELL_CHARS:
                return False
    return True


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _from_pymupdf(page: pymupdf.Page) -> list[TableResult]:
    results: list[TableResult] = []
    try:
        finder = page.find_tables()
    except Exception:
        return results
    for table in getattr(finder, "tables", []) or []:
        try:
            rows = _normalize(table.extract())
        except Exception:
            continue
        if _valid(rows, page):
            results.append(TableResult(bbox=tuple(table.bbox), rows=rows, source="pymupdf"))
    return results


def _from_pdfplumber(plumber_page) -> list[TableResult]:
    results: list[TableResult] = []
    if plumber_page is None:
        return results
    try:
        tables = plumber_page.find_tables()
    except Exception:
        return results
    for table in tables:
        try:
            rows = _normalize(table.extract())
        except Exception:
            continue
        if len(rows) >= 2 and max((len(r) for r in rows), default=0) >= 2:
            cleaned = [r for r in rows if any(r)]
            if len(cleaned) < 2:
                continue
            if any(len(c) > MAX_CELL_CHARS for r in cleaned for c in r):
                continue
            results.append(
                TableResult(bbox=tuple(table.bbox), rows=cleaned, source="pdfplumber")
            )
    return results


def extract_tables(page: pymupdf.Page, plumber_page=None) -> list[TableResult]:
    """Detect tables from PyMuPDF and pdfplumber, validated and de-duplicated."""
    candidates = _from_pymupdf(page) + _from_pdfplumber(plumber_page)

    kept: list[TableResult] = []
    for cand in sorted(candidates, key=lambda t: t.score, reverse=True):
        if any(_iou(cand.bbox, k.bbox) > 0.5 for k in kept):
            continue
        kept.append(cand)
    kept.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
    return kept


def table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [(r + [""] * width)[:width] for r in rows]
    header = padded[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
