from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api.deps import ALLOWED_IMAGE_SUFFIXES
from .services.conversion_service import convert_image, convert_pdf
from .storage import storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-md-conversion",
        description=(
            "Konversi PDF ke Markdown, atau OCR gambar standalone "
            "(pipeline sama dengan endpoint)."
        ),
    )
    parser.add_argument("input", type=Path, help="Path file PDF atau gambar")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Path file .md keluaran")
    parser.add_argument("--scan-images", action="store_true", help="Aktifkan OCR halaman scan")
    parser.add_argument(
        "--ocr-images", action="store_true", help="OCR tiap gambar embedded + flowchart"
    )
    parser.add_argument(
        "--ocr-scope",
        choices=["all", "flowchart"],
        default="all",
        help="Cakupan OCR gambar (default: all)",
    )
    parser.add_argument("--no-images", action="store_true", help="Jangan ekstrak gambar")
    parser.add_argument("--json", action="store_true", help="Cetak metadata JSON ke stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.input.is_file():
        print(f"error: file tidak ditemukan: {args.input}", file=sys.stderr)
        return 2

    suffix = args.input.suffix.lower()
    is_image = suffix in ALLOWED_IMAGE_SUFFIXES
    is_pdf = suffix == ".pdf"

    if not is_pdf and not is_image:
        print(
            "error: input harus PDF atau gambar "
            f"({', '.join(sorted(ALLOWED_IMAGE_SUFFIXES))})",
            file=sys.stderr,
        )
        return 2

    if is_image and (args.scan_images or args.ocr_images or args.no_images):
        print(
            "error: flag --scan-images / --ocr-images / --no-images hanya untuk PDF",
            file=sys.stderr,
        )
        return 2

    output = args.output or args.input.with_suffix(".md")
    doc_id = storage.new_doc_id()

    if is_image:
        result = convert_image(
            image_bytes=args.input.read_bytes(),
            doc_id=doc_id,
            filename=args.input.name,
        )
    else:
        result = convert_pdf(
            pdf_path=args.input,
            doc_id=doc_id,
            scan_images=args.scan_images,
            extract_images=not args.no_images,
            ocr_images=args.ocr_images,
            ocr_scope=args.ocr_scope,
        )

    output.write_text(result.markdown, encoding="utf-8")

    meta = {
        "doc_id": doc_id,
        "output": str(output),
        "pages": result.pages,
        "scanned_pages": result.scanned_pages,
        "tables": result.tables,
        "images_kept": result.images_kept,
        "images_skipped": result.images_skipped,
        "warnings": result.warnings,
    }

    if args.json:
        print(json.dumps(meta, indent=2, ensure_ascii=False))
    else:
        print(f"Markdown: {output}")
        print(
            f"pages={result.pages} scanned={result.scanned_pages} "
            f"tables={result.tables} images_kept={result.images_kept} "
            f"images_skipped={result.images_skipped}"
        )
        for warning in result.warnings:
            print(f"  warning: {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
