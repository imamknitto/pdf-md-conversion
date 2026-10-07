from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import config, storage
from .extractor import extract_text
from .ocr import ocr_image
from .tables import extract_tables, table_to_markdown


@dataclass
class ConvertResult:
    doc_id: str
    markdown: str
    images: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    scanned_pages: int = 0
    tables: int = 0
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


def _markitdown_page(doc: pymupdf.Document, index: int) -> str:
    try:
        from markitdown import MarkItDown
    except Exception:
        return ""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=index, to_page=index)
        single.save(tmp.name)
        single.close()
        return MarkItDown().convert(tmp.name).text_content.strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def convert_pdf(
    pdf_path: Path,
    doc_id: str,
    scan_images: bool = False,
    extract_images: bool = True,
) -> ConvertResult:
    result = ConvertResult(doc_id=doc_id, markdown="")
    dirs = storage.ensure_doc_dirs(doc_id)
    recurring = set()

    import pdfplumber

    with pymupdf.open(pdf_path) as doc, pdfplumber.open(pdf_path) as plumber:
        result.pages = doc.page_count
        recurring = _recurring_xrefs(doc)
        image_cap_hit = False

        for index, page in enumerate(doc):
            number = index + 1
            parts: list[str] = [f"## Page {number}"]

            raw_text = page.get_text("text").strip()
            page_area = abs(page.rect.width * page.rect.height) or 1.0

            has_cover_image = False
            for info in page.get_images(full=True):
                if _max_image_ratio(page, info[0]) >= config.PAGE_COVERAGE:
                    has_cover_image = True
                    break
            is_scan = len(raw_text) < config.MIN_TEXT_CHARS and has_cover_image

            tables = extract_tables(page, plumber.pages[index])
            exclude = [t.bbox for t in tables]

            if is_scan:
                result.scanned_pages += 1
                if scan_images:
                    pix = page.get_pixmap(dpi=config.OCR_DPI)
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
                    parts.append(text or "_OCR tidak menghasilkan teks._")
                else:
                    parts.append("_Halaman scan; OCR dimatikan._")
                    result.warnings.append(
                        f"Halaman {number}: halaman scan, OCR dimatikan."
                    )
            else:
                body = extract_text(page, exclude)
                if not body and not tables:
                    body = _markitdown_page(doc, index)
                if body:
                    parts.append(body)

            for table in tables:
                result.tables += 1
                parts.append(table_to_markdown(table.rows))

            if extract_images:
                seen: set[int] = set()
                img_no = 0
                for info in page.get_images(full=True):
                    xref = info[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    if xref in recurring:
                        result.images_skipped += 1
                        continue
                    if _max_image_ratio(page, xref) < config.IMAGE_MIN_AREA_RATIO:
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
                        data = doc.extract_image(xref)
                    except Exception:
                        result.images_skipped += 1
                        continue
                    if len(data["image"]) > config.MAX_IMAGE_BYTES:
                        result.warnings.append(
                            f"Halaman {number}: gambar {xref} > batas ukuran, dilewati."
                        )
                        result.images_skipped += 1
                        continue
                    img_no += 1
                    ext = data["ext"]
                    filename = f"page-{number:03d}-image-{img_no:03d}.{ext}"
                    storage.write_bytes(dirs["images"], filename, data["image"])
                    result.images_kept += 1
                    result.images.append(
                        {
                            "page": number,
                            "kind": "image",
                            "filename": filename,
                            "url": storage.public_url(doc_id, "images", filename),
                            "width": data["width"],
                            "height": data["height"],
                            "size_bytes": len(data["image"]),
                        }
                    )
                    parts.append(f"![Gambar halaman {number}]({result.images[-1]['url']})")

            parts.append("---")
            result.markdown += "\n\n".join(parts) + "\n\n"

    # Persist markdown apa adanya (newline asli) agar klien bisa unduh
    # file langsung tanpa harus parse field JSON `markdown` (yang di
    # JSON memang selalu ter-escape jadi `\n` — itu bukan bug konversi).
    (dirs["base"] / storage.MARKDOWN_FILENAME).write_text(
        result.markdown, encoding="utf-8"
    )

    return result
