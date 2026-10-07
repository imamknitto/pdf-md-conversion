from pathlib import Path
import pymupdf

pdf_path = Path("brd-lms.pdf")
image_dir = Path("images")
output_path = Path("brd-lms.md")

image_dir.mkdir(exist_ok=True)

doc = pymupdf.open(pdf_path)
markdown_parts = []

for page_number, page in enumerate(doc, start=1):
    markdown_parts.append(f"\n## Page {page_number}\n")

    text = page.get_text("text").strip()

    if text:
        markdown_parts.append(text)
    else:
        markdown_parts.append("_Tidak ada text layer pada halaman ini._")

    image_links = []
    seen_xrefs = set()

    for image_number, image_info in enumerate(
        page.get_images(full=True),
        start=1,
    ):
        xref = image_info[0]

        if xref in seen_xrefs:
            continue

        seen_xrefs.add(xref)

        image_data = doc.extract_image(xref)
        extension = image_data["ext"]

        filename = (
            f"page-{page_number:03d}-image-{image_number:03d}.{extension}"
        )

        image_path = image_dir / filename
        image_path.write_bytes(image_data["image"])

        relative_path = f"images/{filename}"
        image_links.append(
            f"![Image halaman {page_number}]({relative_path})"
        )

    if image_links:
        markdown_parts.append("\n\n" + "\n\n".join(image_links))

    markdown_parts.append("\n\n---\n")

doc.close()

output_path.write_text(
    "\n".join(markdown_parts),
    encoding="utf-8",
)

print(f"Markdown berhasil dibuat: {output_path}")
