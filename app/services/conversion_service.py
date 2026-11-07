from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from ..converters import flowchart as flowchart_mod
from ..converters.extractor import extract_text
from ..converters.ocr import ocr_image_boxes_ex, ocr_image_from_pil
from ..converters.tables import extract_tables, table_to_markdown
from ..core import config
from ..core.errors import JobCancelled
from ..storage import storage


@dataclass
class PageContext:
    """Per-page processing context shared across pipeline stages."""

    number: int
    page: pymupdf.Page
    plumber_page: Any
    raw_text: str
    is_scan: bool
    has_cover_image: bool
    tables: list
    exclude_rects: list
    recurring_xrefs: set[int]
    words: list = field(default_factory=list)
    image_infos: list = field(default_factory=list)
    drawings: list | None = None


@dataclass
class ConvertResult:
    doc_id: str
    markdown: str = ""
    images: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    scanned_pages: int = 0
    tables: int = 0
    images_kept: int = 0
    images_skipped: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class PageResult:
    """Result of processing a single page."""

    markdown_parts: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scanned: bool = False
    tables_count: int = 0
    images_kept: int = 0
    images_skipped: int = 0


def _precompute_images(doc: pymupdf.Document) -> tuple[set[int], dict[int, list]]:
    """Fetch each page's image list once and derive recurring xrefs.

    Returns (recurring_xrefs, {page_index: get_images(full=True)}) so page
    analysis reuses the same list instead of parsing each page twice.
    """
    page_count = doc.page_count
    per_page: dict[int, list] = {}
    seen: dict[int, int] = {}
    for index, page in enumerate(doc):
        infos = page.get_images(full=True)
        per_page[index] = infos
        for info in infos:
            seen[info[0]] = seen.get(info[0], 0) + 1
    if page_count <= 1:
        return set(), per_page
    recurring = {
        x for x, count in seen.items() if count / page_count > config.RECURRING_XREF_RATIO
    }
    return recurring, per_page


def _max_image_ratio(page: pymupdf.Page, xref: int) -> float:
    rects = page.get_image_rects(xref)
    if not rects:
        return 0.0
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    return max(abs(r.width * r.height) for r in rects) / page_area


def _page_drawings(ctx: PageContext) -> list:
    """Vector drawings for a page, fetched at most once per page."""
    if ctx.drawings is None:
        try:
            ctx.drawings = ctx.page.get_drawings() or []
        except Exception:
            ctx.drawings = []
    return ctx.drawings


def extract_pdf_metadata(doc: pymupdf.Document) -> dict:
    """Extract PDF metadata (title, author, dates, etc.)."""
    try:
        meta = doc.metadata or {}
        # Convert datetime strings to ISO format if needed
        result = {}
        for key in (
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creationDate",
            "modDate",
        ):
            value = meta.get(key)
            if value:
                result[key] = value
        return result
    except Exception:
        return {}


def analyze_page(
    page: pymupdf.Page,
    number: int,
    recurring: set[int],
    image_infos_raw: list | None = None,
    plumber_page_getter: Callable[[], Any] | None = None,
) -> PageContext:
    """Stage 1: Analyze page - detect scan, tables, recurring images."""
    words = page.get_text("words")
    if words:
        raw_text = " ".join(w[4] for w in words).strip()
    else:
        raw_text = page.get_text("text").strip()

    image_infos: list[tuple[int, float]] = []
    seen_xrefs: set[int] = set()
    raw_infos = image_infos_raw if image_infos_raw is not None else page.get_images(full=True)
    for info in raw_infos:
        xref = info[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        image_infos.append((xref, _max_image_ratio(page, xref)))

    has_cover_image = any(ratio >= config.PAGE_COVERAGE for _, ratio in image_infos)
    is_scan = len(raw_text) < config.MIN_TEXT_CHARS and has_cover_image

    tables = extract_tables(page, plumber_page_getter=plumber_page_getter)
    exclude = [t.bbox for t in tables]

    return PageContext(
        number=number,
        page=page,
        plumber_page=None,
        raw_text=raw_text,
        is_scan=is_scan,
        has_cover_image=has_cover_image,
        tables=tables,
        exclude_rects=exclude,
        recurring_xrefs=recurring,
        words=words,
        image_infos=image_infos,
    )


def _pil_from_pixmap(pix: pymupdf.Pixmap):
    """Wrap pixmap samples in a PIL image without an intermediate PNG encode."""
    from PIL import Image as _Image

    return _Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def process_scan_page(ctx: PageContext, doc_id: str, scan_images: bool) -> PageResult:
    """Stage 2a: Process scan page - OCR full page if enabled."""
    result = PageResult()
    number = ctx.number

    if ctx.is_scan:
        result.scanned = True
        if scan_images:
            pix = ctx.page.get_pixmap(
                dpi=config.OCR_DPI,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            if pix.width * pix.height > config.MAX_IMAGE_PIXELS:
                result.markdown_parts.append("_Halaman scan terlalu besar untuk diproses._")
                result.warnings.append(
                    f"Halaman {number}: gambar scan melebihi batas "
                    f"{config.MAX_IMAGE_PIXELS} piksel, dilewati."
                )
                return result
            data = pix.tobytes("png")
            filename = f"page-{number:03d}.png"
            result.images.append(
                {
                    "page": number,
                    "kind": "scan",
                    "filename": filename,
                    "url": storage.public_url(doc_id, "scans", filename),
                    "width": pix.width,
                    "height": pix.height,
                    "size_bytes": len(data),
                    "data_base64": base64.b64encode(data).decode("ascii"),
                }
            )
            text = ocr_image_from_pil(_pil_from_pixmap(pix))
            result.markdown_parts.append(text or "_OCR tidak menghasilkan teks._")
        else:
            result.markdown_parts.append("_Halaman scan; OCR dimatikan._")
            result.warnings.append(f"Halaman {number}: halaman scan, OCR dimatikan.")
    return result


def process_text_page(ctx: PageContext) -> PageResult:
    """Stage 2b: Process text page - extract text (reflow)."""
    result = PageResult()
    if not ctx.is_scan:
        body = extract_text(ctx.page, ctx.exclude_rects, ctx.words)
        if body:
            result.markdown_parts.append(body)
    return result


def process_tables(ctx: PageContext) -> PageResult:
    """Stage 3: Process tables - convert to markdown."""
    result = PageResult()
    for table in ctx.tables:
        result.tables_count += 1
        result.markdown_parts.append(table_to_markdown(table.rows))
    return result


def process_images(
    ctx: PageContext,
    doc_id: str,
    extract_images: bool,
    ocr_images: bool,
    ocr_scope: str,
    ocr_done: int,
    ocr_capped: int,
    ocr_tiny: int,
    image_cap_hit: bool,
    skipped_nonflow: int,
    images_kept_so_far: int = 0,
) -> tuple[PageResult, int, int, int, bool, int]:
    """Stage 4: Process embedded images - extract, optionally OCR, flowchart."""
    result = PageResult()
    number = ctx.number

    if not extract_images:
        return result, ocr_done, ocr_capped, ocr_tiny, image_cap_hit, skipped_nonflow

    img_no = 0

    for xref, ratio in ctx.image_infos:
        if xref in ctx.recurring_xrefs:
            result.images_skipped += 1
            continue

        if ratio < config.IMAGE_MIN_AREA_RATIO:
            result.images_skipped += 1
            continue

        if images_kept_so_far + result.images_kept >= config.MAX_IMAGES_PER_DOC:
            if not image_cap_hit:
                result.warnings.append(
                    f"Batas {config.MAX_IMAGES_PER_DOC} gambar tercapai; sisa dilewati."
                )
                image_cap_hit = True
            result.images_skipped += 1
            continue

        try:
            data = ctx.page.parent.extract_image(xref)
        except Exception:
            result.images_skipped += 1
            continue

        if data["width"] * data["height"] > config.MAX_IMAGE_PIXELS:
            result.warnings.append(
                f"Halaman {number}: gambar {xref} melebihi batas "
                f"{config.MAX_IMAGE_PIXELS} piksel, dilewati."
            )
            result.images_skipped += 1
            continue

        if len(data["image"]) > config.MAX_IMAGE_BYTES:
            result.warnings.append(f"Halaman {number}: gambar {xref} > batas ukuran, dilewati.")
            result.images_skipped += 1
            continue

        img_no += 1
        ext = data["ext"]
        filename = f"page-{number:03d}-image-{img_no:03d}.{ext}"
        result.images_kept += 1

        entry: dict = {
            "page": number,
            "kind": "image",
            "filename": filename,
            "url": storage.public_url(doc_id, "images", filename),
            "width": data["width"],
            "height": data["height"],
            "size_bytes": len(data["image"]),
            "ocr_text": None,
            "flowchart_mermaid": None,
            "data_base64": base64.b64encode(data["image"]).decode("ascii"),
        }
        result.images.append(entry)
        result.markdown_parts.append(f"![Gambar halaman {number}]({entry['url']})")

        if ocr_images:
            if ocr_done >= config.OCR_MAX_IMAGES:
                ocr_capped += 1
                continue
            ocr_done += 1
            try:
                boxes, ocr_status = ocr_image_boxes_ex(data["image"])
            except Exception:
                boxes, ocr_status = [], "broken"
            if ocr_status == "tiny":
                ocr_tiny += 1
            if boxes:
                entry["ocr_text"] = "\n".join(b["text"] for b in boxes)
                write_block = True
                if ocr_scope == "flowchart":
                    try:
                        rects = ctx.page.get_image_rects(xref)
                        verdict = flowchart_mod.is_flowchart(
                            boxes,
                            page=ctx.page,
                            img_rect=rects[0] if rects else None,
                            drawings=_page_drawings(ctx),
                        )
                    except Exception:
                        verdict = {"yes": False}
                    write_block = bool(verdict.get("yes"))
                if write_block:
                    quoted = "\n".join(f"> {b['text']}" for b in boxes)
                    result.markdown_parts.append(f"> OCR:\n{quoted}")
                    if config.FLOWCHART_ENABLE:
                        try:
                            fc = flowchart_mod.from_ocr_boxes(boxes)
                        except Exception:
                            fc = None
                        if fc and fc.get("mermaid"):
                            entry["flowchart_mermaid"] = fc["mermaid"]
                else:
                    skipped_nonflow += 1
            else:
                result.markdown_parts.append("> OCR: _tidak menghasilkan teks._")

    if skipped_nonflow:
        result.warnings.append(
            f"Halaman {number}: {skipped_nonflow} gambar dilewati (bukan flowchart)."
        )

    return result, ocr_done, ocr_capped, ocr_tiny, image_cap_hit, skipped_nonflow


def assemble_page_result(ctx: PageContext, page_results: list[PageResult]) -> str:
    """Stage 6: Assemble markdown parts from all stages."""
    all_parts = [f"## Page {ctx.number}"]
    for pr in page_results:
        all_parts.extend(pr.markdown_parts)
    all_parts.append("---")
    return "\n\n".join(all_parts) + "\n\n"


def finalize_result(
    result: ConvertResult,
    ocr_capped: int,
    ocr_tiny: int,
) -> ConvertResult:
    """Stage 7: Finalize - add summary warnings."""
    if ocr_capped:
        result.warnings.append(
            f"Batas {config.OCR_MAX_IMAGES} gambar OCR tercapai; "
            f"{ocr_capped} gambar disimpan tanpa teks OCR."
        )
    if ocr_tiny:
        result.warnings.append(
            f"{ocr_tiny} gambar terlalu kecil untuk OCR, disimpan tanpa teks OCR."
        )

    return result


def convert_image(
    image_bytes: bytes,
    doc_id: str,
    filename: str,
) -> ConvertResult:
    """OCR a standalone image into the same ConvertResult shape as convert_pdf."""
    import io

    from PIL import Image as _Image

    safe_name = Path(filename).name or "image.png"
    suffix = Path(safe_name).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        safe_name = f"{Path(safe_name).stem}.png"
        suffix = ".png"
    stored_name = f"image-001{suffix}"

    result = ConvertResult(doc_id=doc_id, markdown="", pages=1, images_kept=1)

    try:
        with _Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
    except Exception:
        width, height = 0, 0

    entry: dict = {
        "page": 1,
        "kind": "image",
        "filename": stored_name,
        "url": storage.public_url(doc_id, "images", stored_name),
        "width": width,
        "height": height,
        "size_bytes": len(image_bytes),
        "ocr_text": None,
        "flowchart_mermaid": None,
        "data_base64": base64.b64encode(image_bytes).decode("ascii"),
    }
    result.images.append(entry)

    md_parts = [f"![Gambar]({entry['url']})"]

    try:
        boxes, ocr_status = ocr_image_boxes_ex(image_bytes)
    except Exception:
        boxes, ocr_status = [], "broken"

    if ocr_status == "tiny":
        result.warnings.append("Gambar terlalu kecil untuk OCR, disimpan tanpa teks OCR.")
        md_parts.append("> OCR: _tidak menghasilkan teks._")
    elif ocr_status in ("broken", "circuit_open"):
        result.warnings.append(f"OCR gagal (status={ocr_status}).")
        md_parts.append("> OCR: _tidak menghasilkan teks._")
    elif not boxes:
        result.warnings.append("OCR tidak menghasilkan teks.")
        md_parts.append("> OCR: _tidak menghasilkan teks._")
    else:
        entry["ocr_text"] = "\n".join(b["text"] for b in boxes)
        quoted = "\n".join(f"> {b['text']}" for b in boxes)
        md_parts.append(f"> OCR:\n{quoted}")
        if config.FLOWCHART_ENABLE:
            try:
                fc = flowchart_mod.from_ocr_boxes(boxes)
            except Exception:
                fc = None
            if fc and fc.get("mermaid"):
                entry["flowchart_mermaid"] = fc["mermaid"]
                md_parts.append(f"```mermaid\n{fc['mermaid']}\n```")

    result.markdown = "\n\n".join(md_parts) + "\n"
    return result


class _LazyPlumber:
    """Open pdfplumber only if a page actually needs the second table engine."""

    def __init__(self, path: Path):
        self._path = path
        self._ctx = None
        self._pdf = None

    def page(self, index: int):
        if self._pdf is None:
            import pdfplumber

            self._ctx = pdfplumber.open(self._path)
            self._pdf = self._ctx.__enter__()
        return self._pdf.pages[index]

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None
            self._pdf = None


def convert_pdf(
    pdf_path: Path,
    doc_id: str,
    scan_images: bool = False,
    extract_images: bool = True,
    ocr_images: bool = False,
    ocr_scope: str = "all",
    on_page: Callable[[int, int], None] | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> ConvertResult:
    """Main pipeline: orchestrate all stages per page.

    Args:
        check_cancelled: Optional callback returning True if job should be cancelled.
            Called at start of each page and before heavy operations.
    """
    result = ConvertResult(doc_id=doc_id, markdown="")
    plumber = _LazyPlumber(pdf_path)

    try:
        with pymupdf.open(pdf_path) as doc:
            result.pages = doc.page_count
            result.metadata = extract_pdf_metadata(doc)
            recurring, images_per_page = _precompute_images(doc)

            ocr_done = 0
            ocr_capped = 0
            ocr_tiny = 0
            image_cap_hit = False

            for index, page in enumerate(doc):
                # Check cancellation at start of each page
                if check_cancelled and check_cancelled():
                    raise JobCancelled("Job cancelled")

                number = index + 1

                # Stage 1: Analyze (reuse the image list fetched above)
                ctx = analyze_page(
                    page,
                    number,
                    recurring,
                    image_infos_raw=images_per_page.get(index),
                    plumber_page_getter=lambda index=index: plumber.page(index),
                )
                if ctx.is_scan:
                    result.scanned_pages += 1

                # Stage 2a/2b: Scan or Text
                page_results: list[PageResult] = []
                page_results.append(process_scan_page(ctx, doc_id, scan_images))
                page_results.append(process_text_page(ctx))

                # Stage 3: Tables
                page_results.append(process_tables(ctx))
                result.tables += page_results[-1].tables_count

                # Stage 4: Images (heavy - check before)
                if check_cancelled and check_cancelled():
                    raise JobCancelled("Job cancelled")
                (
                    img_result,
                    ocr_done,
                    ocr_capped,
                    ocr_tiny,
                    image_cap_hit,
                    skipped_nonflow,
                ) = process_images(
                    ctx,
                    doc_id,
                    extract_images,
                    ocr_images,
                    ocr_scope,
                    ocr_done,
                    ocr_capped,
                    ocr_tiny,
                    image_cap_hit,
                    0,
                    images_kept_so_far=result.images_kept,
                )
                result.images_kept += img_result.images_kept
                result.images_skipped += img_result.images_skipped
                page_results.append(img_result)

                # Stage 5: Assemble
                page_md = assemble_page_result(ctx, page_results)
                result.markdown += page_md

                # Collect images and warnings
                for pr in page_results:
                    result.images.extend(pr.images)
                    result.warnings.extend(pr.warnings)

                # Progress callback
                if on_page is not None:
                    try:
                        on_page(number, result.pages)
                    except JobCancelled:
                        raise
                    except Exception:
                        pass
    finally:
        plumber.close()

    # Stage 7: Finalize
    return finalize_result(result, ocr_capped, ocr_tiny)
