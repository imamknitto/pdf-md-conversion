# Kontrak API — pdf-md-conversion

Base URL: `http://127.0.0.1:8379` (default lokal). Semua endpoint berada di
bawah `/v1`.

Converter tidak menyimpan atau melayani artefak konversi. Nilai `/public/...`
adalah URL relatif untuk consumer. Consumer menyimpan `markdown`, decode
`images[].data_base64`, lalu melayani URL tersebut.

## Konvensi umum

Auth (bila `PDF_MD_CONVERTER_API_KEY` di-set; `/v1/health` selalu terbuka):

```http
Authorization: Bearer <PDF_MD_CONVERTER_API_KEY>
```

Error (semua endpoint):

```json
{ "detail": "…pesan error…" }
```

ID:

- `doc_id` / `job_id`: 32 karakter hex (contoh `9f3c…a1`), tak tertebak.
- `doc_id` tidak valid → `404`.

Rate limit (429 bila terlampaui):

- `POST /v1/convert`: 10 req/menit
- `POST /v1/ocr`: 10 req/menit
- `POST /v1/convert/jobs`: 20 req/menit

## 1. GET /v1/health

Tanpa auth. Untuk health check / readiness.

Contoh respons `200`:

```json
{ "status": "ok", "service": "pdf-md-conversion", "version": "0.1.0" }
```

## 2. POST /v1/convert

Konversi sinkron (`multipart/form-data`). Untuk PDF kecil/sedang.

Request:

| Field | Tipe | Wajib | Default | Keterangan |
|---|---|---|---|---|
| `file` | file | ya | – | PDF, maksimal 30 MB, tanpa password |
| `scan_images` | bool | tidak | `false` | OCR halaman hasil scan |
| `extract_images` | bool | tidak | `true` | Ekstrak gambar embedded |
| `ocr_images` | bool | tidak | `false` | OCR tiap gambar (butuh `extract_images=true`) |
| `ocr_scope` | string | tidak | `all` | `all` atau `flowchart` (OCR hanya gambar diagram) |

Contoh:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/v1/convert" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" `
  -F "file=@dokumen.pdf" -F "scan_images=false" -o resp.json
```

Contoh respons `200`:

```json
{
  "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5",
  "markdown": "## Page 1\n\nIsi dokumen…\n",
  "markdown_url": "/public/9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5/doc.md",
  "stats": { "pages": 12, "scanned_pages": 0, "tables": 2, "images_kept": 5, "images_skipped": 3 },
  "images": [
    { "page": 3, "kind": "image", "filename": "page-003-image-001.jpeg",
      "url": "/public/9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5/images/page-003-image-001.jpeg",
      "width": 1268, "height": 658, "size_bytes": 63488,
      "ocr_text": null, "flowchart_mermaid": null,
      "data_base64": "/9j/4AAQSkZJRgABAQ..." }
  ],
  "warnings": [],
  "expires_at": "2026-10-14T09:00:00+00:00",
  "metadata": { "title": "Contoh", "author": "ACME", "creator": "Word" }
}
```

Catatan field:

- `markdown` ter-escape (`\n`) sebagai JSON normal. Consumer menyimpannya
  sebagai `doc.md`.
- `markdown_url` dan `images[].url` adalah path relatif yang harus dilayani
  consumer; jangan prefix dengan base URL converter.
- `images[].kind`: `image` (embedded) atau `scan` (halaman scan).
- `images[].data_base64`: byte gambar/scan dalam Base64. Decode dan simpan di
  path `images[].url`; jangan teruskan field ini ke browser setelah tersimpan.
- `images[].ocr_text`: teks OCR bila `ocr_images=true`; `flowchart_mermaid`: diagram Mermaid bila gambar terdeteksi flowchart.
- `metadata`: metadata PDF (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creationDate`, `modDate`); `{}` bila kosong.
- `warnings`: mis. gambar dilewati karena melebihi cap OCR.

Error:

| Status | Kapan |
|---|---|
| `400` | Bukan PDF / content-type tak didukung / `ocr_scope` salah / PDF berpassword |
| `401` | Token salah / tidak ada (bila key di-set) |
| `413` | File > 30 MB |
| `429` | Rate limit |
| `500` | Gagal memproses PDF |

## 3. POST /v1/ocr

OCR sinkron untuk gambar standalone (`multipart/form-data`). Bentuk respons
sama dengan `/v1/convert` (`ConvertResponse`).

Request:

| Field | Tipe | Wajib | Default | Keterangan |
|---|---|---|---|---|
| `file` | file | ya | – | PNG / JPEG / WebP / TIFF, maksimal `MAX_IMAGE_MB` (default 10 MB) |

Contoh:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/v1/ocr" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" `
  -F "file=@scan.png" -o resp.json
```

Contoh respons `200`: field sama seperti `/v1/convert`. Ringkasannya:

- `stats.pages` = `1`, `tables` = `0`, `scanned_pages` = `0`, `images_kept` = `1`
- `images[0].kind` = `"image"`, `page` = `1`, `ocr_text` berisi hasil OCR (atau `null`)
- `images[0].flowchart_mermaid` terisi bila diagram terdeteksi
- `markdown` memuat link gambar + blok `> OCR:` (+ fenced `mermaid` bila ada)

Error:

| Status | Kapan |
|---|---|
| `400` | Bukan gambar / suffix atau content-type tak didukung / file corrupt |
| `401` | Token salah / tidak ada (bila key di-set) |
| `413` | File > `MAX_IMAGE_MB` |
| `429` | Rate limit |
| `500` | Gagal memproses gambar |

## 4. POST /v1/convert/jobs

Konversi async untuk PDF besar. Field sama seperti `/v1/convert`, plus:

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `webhook_url` | string | tidak | URL `http(s)` untuk callback saat job selesai (lihat §9) |

Langsung `202`, tanpa menunggu selesai:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/v1/convert/jobs" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" `
  -F "file=@besar.pdf" -F "ocr_images=true" -o job.json
```

Contoh respons `202`:

```json
{ "job_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90", "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5", "status": "queued" }
```

Error: sama seperti `/v1/convert`, plus `429` bila antrean penuh (`CONVERT_MAX_QUEUE`).

## 5. GET /v1/convert/jobs/{job_id}

Polling status job. Contoh respons `200` (processing):

```json
{
  "job_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5",
  "filename": "besar.pdf",
  "status": "processing",
  "progress": { "pages_done": 40, "pages_total": 101 },
  "result": null,
  "error": null
}
```

`status`: `queued` → `processing` → `done` | `error`. Saat `done`, `result` berisi
objek `ConvertResponse` penuh seperti §2. Saat `error`, `error` berisi pesan
(mis. `"Gagal memproses PDF."`).

Error: `401` (auth), `404` (`job_id` tidak valid / tak dikenal / expired).

## 6. DELETE /v1/convert/jobs/{job_id}

Batalkan job yang masih `queued`/`processing` (204 tanpa body).
Job `done`/`error` → `409`. Job tak dikenal/expired → `404`.

```powershell
curl.exe -X DELETE "http://127.0.0.1:8379/v1/convert/jobs/<job_id>" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY"
```

## 7. GET /v1/documents

Stub legacy. Converter tidak menyimpan artefak; respons selalu:

```json
{ "documents": [] }
```

Error: `401` (auth).

## 8. GET /v1/download/{doc_id}

Stub legacy. Selalu `404`. Gunakan field `markdown` dari response konversi.

## 9. Webhook job (callback)

Bila `POST /v1/convert/jobs` menyertakan `webhook_url`, service `POST` ke URL itu
saat job `done`/`error` (dengan retry + backoff). Contoh payload:

```json
{
  "job_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5",
  "filename": "besar.pdf",
  "status": "done",
  "progress": { "pages_done": 101, "pages_total": 101 },
  "result": { "doc_id": "9f3c…", "markdown": "## Page 1\n\n…", "stats": { "pages": 101 } },
  "error": null
}
```

(`result` = objek `ConvertResponse` penuh seperti §2.)

## 10. DELETE /v1/documents/{doc_id}

Stub legacy. Selalu `404`; penghapusan artefak menjadi tanggung jawab consumer.

## 11. Handoff artefak

Untuk setiap hasil sinkron atau job async berstatus `done`, consumer:

1. Menulis `markdown` ke path `markdown_url`.
2. Decode setiap `images[].data_base64`.
3. Menulis byte hasil decode ke path `images[].url`.
4. Menghapus `data_base64` sebelum response diteruskan ke browser.
5. Menghapus artefak berdasarkan `expires_at`.
