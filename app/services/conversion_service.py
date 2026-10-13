from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from ..converters import flowchart as flowchart_mod
from ..converters.extractor import extract_text
from ..converters.ocr import ocr_image, ocr_image_boxes_ex
from ..converters.tables import extract_tables, table_to_markdown
from ..core import config
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


def _recurring_xrefs(doc: pymupdf.Document) -> set[int]:
    page_count = doc.page_count
    if page_count <= 1:
        return set()
    seen: dict[int, int] = {}
    for page in doc:
        for info in page.get_images(full=True):
            seen[info[0]] = seen.get(info[0], 0) + 1
    return {x for x, count in seen.items() if count / page_count > config.RECURRING_XREF_RATIO}


def _max_image_ratio(page: pymupdf.Page, xref: int) -> float:
    rects = page.get_image_rects(xref)
    if not rects:
        return 0.0
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    return max(abs(r.width * r.height) for r in rects) / page_area


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
    plumber_page: Any,
    number: int,
    recurring: set[int],
) -> PageContext:
    """Stage 1: Analyze page - detect scan, tables, recurring images."""
    raw_text = page.get_text("text").strip()

    has_cover_image = False
    for info in page.get_images(full=True):
        if _max_image_ratio(page, info[0]) >= config.PAGE_COVERAGE:
            has_cover_image = True
            break
    is_scan = len(raw_text) < config.MIN_TEXT_CHARS and has_cover_image

    tables = extract_tables(page, plumber_page)
    exclude = [t.bbox for t in tables]

    return PageContext(
        number=number,
        page=page,
        plumber_page=plumber_page,
        raw_text=raw_text,
        is_scan=is_scan,
        has_cover_image=has_cover_image,
        tables=tables,
        exclude_rects=exclude,
        recurring_xrefs=recurring,
    )


def process_scan_page(ctx: PageContext, doc_id: str, dirs: dict, scan_images: bool) -> PageResult:
    """Stage 2a: Process scan page - OCR full page if enabled."""
    result = PageResult()
    number = ctx.number

    if ctx.is_scan:
        result.scanned = True
        if scan_images:
            pix = ctx.page.get_pixmap(dpi=config.OCR_DPI)
            data = pix.tobytes("png")
            filename = f"page-{number:03d}.png"
            storage.write_bytes(dirs["scans"], filename, data)
            result.images.append(
                {
                    "page": number,
                    "kind": "scan",
                    "filename": filename,
                    "url": storage.public_url(doc_id, "scans", filename),
                    "width": pix.width,
                    "height": pix.height,
                    "size_bytes": len(data),
                }
            )
            text = ocr_image(data)
            result.markdown_parts.append(text or "_OCR tidak menghasilkan teks._")
        else:
            result.markdown_parts.append("_Halaman scan; OCR dimatikan._")
            result.warnings.append(f"Halaman {number}: halaman scan, OCR dimatikan.")
    return result


def process_text_page(ctx: PageContext) -> PageResult:
    """Stage 2b: Process text page - extract text (reflow)."""
    result = PageResult()
    if not ctx.is_scan:
        body = extract_text(ctx.page, ctx.exclude_rects)
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
    dirs: dict,
    extract_images: bool,
    ocr_images: bool,
    ocr_scope: str,
    ocr_done: int,
    ocr_capped: int,
    ocr_tiny: int,
    image_cap_hit: bool,
    skipped_nonflow: int,
) -> tuple[PageResult, int, int, int, bool, int]:
    """Stage 4: Process embedded images - extract, optionally OCR, flowchart."""
    result = PageResult()
    number = ctx.number

    if not extract_images:
        return result, ocr_done, ocr_capped, ocr_tiny, image_cap_hit, skipped_nonflow

    seen: set[int] = set()
    img_no = 0

    for info in ctx.page.get_images(full=True):
        xref = info[0]
        if xref in seen:
            continue
        seen.add(xref)

        if xref in ctx.recurring_xrefs:
            result.images_skipped += 1
            continue

        if _max_image_ratio(ctx.page, xref) < config.IMAGE_MIN_AREA_RATIO:
            result.images_skipped += 1
            continue

        if result.images_kept >= config.MAX_IMAGES_PER_DOC:
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

        if len(data["image"]) > config.MAX_IMAGE_BYTES:
            result.warnings.append(f"Halaman {number}: gambar {xref} > batas ukuran, dilewati.")
            result.images_skipped += 1
            continue

        img_no += 1
        ext = data["ext"]
        filename = f"page-{number:03d}-image-{img_no:03d}.{ext}"
        storage.write_bytes(dirs["images"], filename, data["image"])
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


def process_page_flowchart(
    ctx: PageContext,
    ocr_images: bool,
) -> PageResult:
    """Stage 5: Detect flowchart from page vector graphics (if enabled)."""
    result = PageResult()
    number = ctx.number

    if ocr_images and config.FLOWCHART_ENABLE and not ctx.is_scan and not ctx.tables:
        try:
            fc_page = flowchart_mod.from_page(ctx.page)
        except Exception:
            fc_page = None
        if fc_page and len(fc_page.get("nodes", [])) >= 2:
            if fc_page.get("confidence") == "low":
                result.warnings.append(
                    f"Halaman {number}: struktur flowchart tidak pasti, hanya urutan baca linear."
                )
    return result


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
    dirs: dict,
) -> ConvertResult:
    """Stage 7: Finalize - add summary warnings, persist markdown."""
    if ocr_capped:
        result.warnings.append(
            f"Batas {config.OCR_MAX_IMAGES} gambar OCR tercapai; "
            f"{ocr_capped} gambar disimpan tanpa teks OCR."
        )
    if ocr_tiny:
        result.warnings.append(
            f"{ocr_tiny} gambar terlalu kecil untuk OCR, disimpan tanpa teks OCR."
        )

    (dirs["base"] / storage.MARKDOWN_FILENAME).write_text(result.markdown, encoding="utf-8")
    return result


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
    dirs = storage.ensure_doc_dirs(doc_id)

    import pdfplumber

    with pymupdf.open(pdf_path) as doc, pdfplumber.open(pdf_path) as plumber:
        result.pages = doc.page_count
        result.metadata = extract_pdf_metadata(doc)
        recurring = _recurring_xrefs(doc)

        ocr_done = 0
        ocr_capped = 0
        ocr_tiny = 0
        image_cap_hit = False

        for index, page in enumerate(doc):
            # Check cancellation at start of each page
            if check_cancelled and check_cancelled():
                raise RuntimeError("Job cancelled")

            number = index + 1
            plumber_page = plumber.pages[index]

            # Stage 1: Analyze
            ctx = analyze_page(page, plumber_page, number, recurring)
            if ctx.is_scan:
                result.scanned_pages += 1

            # Stage 2a/2b: Scan or Text
            page_results: list[PageResult] = []
            page_results.append(process_scan_page(ctx, doc_id, dirs, scan_images))
            page_results.append(process_text_page(ctx))

            # Stage 3: Tables
            page_results.append(process_tables(ctx))
            result.tables += page_results[-1].tables_count

            # Stage 4: Images (heavy - check before)
            if check_cancelled and check_cancelled():
                raise RuntimeError("Job cancelled")
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
                dirs,
                extract_images,
                ocr_images,
                ocr_scope,
                ocr_done,
                ocr_capped,
                ocr_tiny,
                image_cap_hit,
                0,
            )
            result.images_kept += img_result.images_kept
            result.images_skipped += img_result.images_skipped
            page_results.append(img_result)

            # Stage 5: Page-level flowchart
            page_results.append(process_page_flowchart(ctx, ocr_images))

            # Stage 6: Assemble
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
                except Exception:
                    pass

    # Stage 7: Finalize
    return finalize_result(result, ocr_capped, ocr_tiny, dirs)
