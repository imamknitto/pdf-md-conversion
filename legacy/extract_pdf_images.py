import fitz
from pathlib import Path

pdf_path = Path("cookbook.pdf")
image_dir = Path("images")
image_dir.mkdir(exist_ok=True)

pdf = fitz.open(pdf_path)
image_number = 0

for page_number, page in enumerate(pdf, start=1):
    for image_index, image_info in enumerate(page.get_images(full=True), start=1):
        xref = image_info[0]
        image_data = pdf.extract_image(xref)

        extension = image_data["ext"]
        image_number += 1

        filename = (
            f"page-{page_number:03d}-image-{image_index:03d}.{extension}"
        )

        output_path = image_dir / filename
        output_path.write_bytes(image_data["image"])

        print(f"Disimpan: {output_path}")

pdf.close()

print(f"Total image: {image_number}")
