from __future__ import annotations

import pymupdf


def _fallback_text(page: pymupdf.Page) -> str:
    text = page.get_text("text", sort=True)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def extract_text(
    page: pymupdf.Page, exclude_rects: list | None = None, words: list | None = None
) -> str:
    """Extract page text via word reflow, skipping words inside excluded rects.

    PyMuPDF's plain ``get_text`` emits one word per line on justified/stretched
    PDFs. Grouping the word list by (block, line) and sorting by x restores
    proper spacing and line order. Pass ``words`` to reuse a page's already
    extracted word list (avoids a second parse).
    """
    rects = [pymupdf.Rect(r) for r in (exclude_rects or [])]
    if words is None:
        words = page.get_text("words")
    if not words:
        return _fallback_text(page)

    def excluded(word) -> bool:
        if not rects:
            return False
        cx = (word[0] + word[2]) / 2
        cy = (word[1] + word[3]) / 2
        point = pymupdf.Point(cx, cy)
        return any(r.contains(point) for r in rects)

    kept = [w for w in words if not excluded(w)]
    if not kept:
        return ""

    # Group by vertical position: this PDF emits each word as its own line, so
    # (block, line) grouping fails. Rounding y0 reconstructs visual lines.
    groups: dict[int, list] = {}
    for w in kept:
        groups.setdefault(round(w[1] / 3.0), []).append(w)

    lines: list[str] = []
    for key in sorted(groups):
        ws = sorted(groups[key], key=lambda x: x[0])
        line = " ".join(" ".join(x[4].split()) for x in ws)
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)
