"""Heuristik flowchart lokal (tanpa LLM/VLM).

Dua jalur:
- boxes OCR raster  -> urutan baca + rantai Mermaid linear (fallback jujur).
- halaman vektor PDF -> node dari shape tertutup + teks di dalamnya,
  edge dari garis/kurva yang menghubungkan dua node (arah bila ada
  kepala panah / bila mengalir atas->bawah).
- is_flowchart() -> skor apakah sebuah gambar layak dianggap diagram/
  flowchart (untuk ocr_scope="flowchart").
"""

from __future__ import annotations

import re

from ..core import config


def _label(text: str, limit: int = 40) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace('"', "'").replace("\n", " ")


def mermaid_from_nodes(nodes: list[dict], edges: list[tuple[int, int, str]]) -> str:
    lines = ["flowchart TD"]
    for i, node in enumerate(nodes):
        lines.append(f'  n{i + 1}["{_label(node.get("text", ""))}"]')
    if edges:
        for a, b, lab in edges:
            if lab:
                lines.append(f"  n{a + 1} -->|{_label(lab, 20)}| n{b + 1}")
            else:
                lines.append(f"  n{a + 1} --> n{b + 1}")
    else:
        for i in range(len(nodes) - 1):
            lines.append(f"  n{i + 1} -.-> n{i + 2}")
    return "\n".join(lines)


def from_ocr_boxes(boxes: list[dict]) -> dict:
    """Susun urutan baca + Mermaid linear dari box OCR raster.

    Jujur: edge adalah urutan baca (garis putus-putus), BUKAN panah
    terdeteksi. confidence selalu 'low' bila hanya ini yang tersedia.
    """
    nodes = [{"text": b["text"], "cx": b["cx"], "cy": b["cy"]} for b in boxes]
    edges = [(i, i + 1, "") for i in range(len(nodes) - 1)]
    ordered = "\n".join(f"[{i + 1}] {n['text']}" for i, n in enumerate(nodes))
    return {
        "nodes": nodes,
        "edges": edges,
        "mermaid": mermaid_from_nodes(nodes, edges) if nodes else "",
        "ordered_text": ordered,
        "confidence": "low" if nodes else "none",
        "method": "ocr-reading-order",
    }


def _nearest_node(nodes: list[dict], x: float, y: float, tol: float = 40.0) -> int:
    best, best_d = -1, tol
    for i, n in enumerate(nodes):
        d = abs(n["cx"] - x) + abs(n["cy"] - y)
        if d < best_d:
            best, best_d = i, d
    return best


def from_page(page, drawings=None, words=None) -> dict:
    """Ekstrak flowchart dari halaman PDF vektor (drawings + text words).

    ``drawings`` / ``words`` dapat diberikan oleh pemanggil untuk menghindari
    parsing ulang halaman yang sama (mis. dipakai bersama is_flowchart()).
    """
    if words is None:
        try:
            words = page.get_text("words") or []
        except Exception:
            words = []
    if drawings is None:
        try:
            drawings = page.get_drawings() or []
        except Exception:
            drawings = []
    drawings = drawings[: config.FLOWCHART_MAX_SHAPES]

    rects = []
    for d in drawings:
        try:
            r = d.get("rect")
            if r is None or r.width < 20 or r.height < 12:
                continue
            rects.append(r)
        except Exception:
            continue

    nodes: list[dict] = []
    for r in rects:
        inside = [
            w for w in words if r.contains((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) and w[4].strip()
        ]
        if not inside:
            continue
        inside.sort(key=lambda w: w[0])
        text = " ".join(" ".join(w[4].split()) for w in inside)
        nodes.append({"text": text, "cx": (r.x0 + r.x1) / 2, "cy": (r.y0 + r.y1) / 2})

    # Sort top-to-bottom, left-to-right dengan toleransi baris.
    tol = config.FLOWCHART_Y_TOL
    nodes.sort(key=lambda n: (round(n["cy"] / max(tol, 1e-6)), n["cx"]))

    edges: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for d in drawings:
        try:
            items = d.get("items") or []
            pts: list[tuple[float, float]] = []
            for it in items:
                op = it[0]
                if op == "l" and len(it) >= 3:
                    pts.append((float(it[1].x), float(it[1].y)))
                    pts.append((float(it[2].x), float(it[2].y)))
                elif op == "c" and len(it) >= 4:
                    pts.append((float(it[1].x), float(it[1].y)))
                    pts.append((float(it[-1].x), float(it[-1].y)))
            if len(pts) < 2:
                continue
            a = _nearest_node(nodes, *pts[0])
            b = _nearest_node(nodes, *pts[-1])
            if a < 0 or b < 0 or a == b or (a, b) in seen:
                continue
            # Label edge: kata yang dekat ke tengah garis.
            mx, my = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
            lab = ""
            best_d = 30.0
            for w in words:
                dmid = abs((w[0] + w[2]) / 2 - mx) + abs((w[1] + w[3]) / 2 - my)
                if dmid < best_d and not any(
                    abs(n["cx"] - (w[0] + w[2]) / 2) + abs(n["cy"] - (w[1] + w[3]) / 2) < 25
                    for n in nodes
                ):
                    best_d, lab = dmid, w[4]
            seen.add((a, b))
            edges.append((a, b, lab))
        except Exception:
            continue

    # Fallback jujur: tanpa edge terdeteksi, rantai baca linear.
    method = "vector-graph" if edges else "vector-reading-order"
    confidence = "medium" if edges else ("low" if nodes else "none")
    return {
        "nodes": nodes,
        "edges": edges,
        "mermaid": mermaid_from_nodes(nodes, edges) if nodes else "",
        "ordered_text": "\n".join(f"[{i + 1}] {n['text']}" for i, n in enumerate(nodes)),
        "confidence": confidence,
        "method": method,
    }


_NUMBERED_RE = re.compile(r"^\s*\(?\d{1,3}\s*[.)\-:]\s*\S.{2,}")
_FLOW_KEYWORDS = (
    "apakah",
    "jika",
    "maka",
    "apabila",
    "start",
    "end",
    "mulai",
    "selesai",
    "proses",
)
_FLOW_LABELS = ("y", "n", "ya", "tidak", "yes", "no")


def is_flowchart(
    boxes: list[dict],
    page=None,
    img_rect=None,
    drawings=None,
) -> dict:
    """Nilai apakah gambar layak dianggap diagram/flowchart.

    Skor gabungan sinyal teks OCR + geometri vektor halaman. Lolos bila
    ``score >= config.FLOWCHART_SCORE_MIN``. Tidak pernah raise (gagal
    hitung -> ``yes=False``) agar aman dipakai di pipeline konversi.
    ``drawings`` dapat diberikan agar ``page.get_drawings()`` tidak diulang.
    """
    score = 0
    strong = 0
    reasons: list[str] = []
    try:
        texts = [str(b.get("text", "")).strip() for b in (boxes or [])]
        texts = [x for x in texts if x]
        numbered = sum(1 for x in texts if _NUMBERED_RE.match(x))
        if numbered >= 2:
            score += 2
            strong += 2
            reasons.append(f"langkah bernomor x{numbered}")
        lowered = " ".join(texts).lower()
        hits = sorted({k for k in _FLOW_KEYWORDS if k in lowered})
        if hits:
            score += 1
            strong += 1
            reasons.append("kata alur: " + ",".join(hits[:4]))
        standalone = {x.strip("()[]'\" ").lower() for x in texts}
        if standalone & set(_FLOW_LABELS):
            score += 1
            strong += 1
            reasons.append("label cabang Y/N")
        if len(texts) >= 3:
            score += 1
            reasons.append(f"{len(texts)} baris teks")
        if page is not None and img_rect is not None:
            if drawings is None:
                try:
                    drawings = page.get_drawings() or []
                except Exception:
                    drawings = []
            overlap = 0
            for d in drawings[: config.FLOWCHART_MAX_SHAPES]:
                try:
                    if img_rect.intersects(d.get("rect")):
                        overlap += 1
                except Exception:
                    continue
            if overlap >= 3:
                score += 1
                strong += 1
                reasons.append(f"{overlap} shape vektor beririsan")
            try:
                page_area = abs(page.rect.width * page.rect.height) or 1.0
                ratio = abs(img_rect.width * img_rect.height) / page_area
                if ratio >= 0.15:
                    score += 1
                    reasons.append("gambar diagram cukup besar")
            except Exception:
                pass
    except Exception:
        return {"yes": False, "score": 0, "reasons": ["gagal skor"]}
    # Sinyal lemah (banyak baris / gambar besar) saja tidak cukup —
    # butuh minimal satu sinyal kuat agar screenshot form ber-badge
    # angka (mis. "1","2" di atas tombol) tidak lolos.
    ok = score >= config.FLOWCHART_SCORE_MIN and strong >= 1
    return {
        "yes": ok,
        "score": score,
        "reasons": reasons,
    }
