# Kontrak API — pdf-md-conversion

Base URL: `http://127.0.0.1:8379` (default lokal). Semua endpoint di bawah `/v1`,
kecuali file statis di `/public/*`.

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
      "ocr_text": null, "flowchart_mermaid": null }
  ],
  "warnings": [],
  "expires_at": "2026-10-14T09:00:00+00:00",
  "metadata": { "title": "Contoh", "author": "ACME", "creator": "Word" }
}
```

Catatan field:

- `markdown` ter-escape (`\n`) — normal JSON, pakai JSON parse; atau ambil file mentah via `markdown_url` (prefix dengan base URL service).
- `images[].kind`: `image` (embedded) atau `scan` (halaman scan).
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
| `507` | Disk penyimpanan penuh |

## 3. POST /v1/convert/jobs

Konversi async untuk PDF besar. Field sama seperti `/v1/convert`, plus:

| Field | Tipe | Wajib | Keterangan |
|---|---|---|---|
| `webhook_url` | string | tidak | URL `http(s)` untuk callback saat job selesai (lihat §8) |

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

## 4. GET /v1/convert/jobs/{job_id}

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

## 5. DELETE /v1/convert/jobs/{job_id}

Batalkan job yang masih `queued`/`processing` (204 tanpa body).
Job `done`/`error` → `409`. Job tak dikenal/expired → `404`.

```powershell
curl.exe -X DELETE "http://127.0.0.1:8379/v1/convert/jobs/<job_id>" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY"
```

## 6. GET /v1/documents

Daftar dokumen yang masih tersedia (terbaru dulu). Contoh respons `200`:

```json
{
  "documents": [
    {
      "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5",
      "markdown_url": "/public/9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5/doc.md",
      "filename": "dokumen.pdf",
      "stats": { "pages": 12, "scanned_pages": 0, "tables": 2, "images_kept": 5, "images_skipped": 3 },
      "warnings": [],
      "created_at": "2026-10-14T08:00:00+00:00",
      "expires_at": "2026-10-14T09:00:00+00:00"
    }
  ]
}
```

Error: `401` (auth).

## 7. GET /v1/download/{doc_id}

Unduh markdown sebagai file `text/markdown` (tanpa escape JSON),
nama file `<doc_id>.md`. Contoh:

```powershell
curl.exe "http://127.0.0.1:8379/v1/download/9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" -o dokumen.md
```

Error: `401` (auth), `404` (`doc_id` tidak valid / tidak ada / expired).

## 8. Webhook job (callback)

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

## 9. DELETE /v1/documents/{doc_id}

Hapus dokumen + semua artefaknya. Contoh respons `200`:

```json
{ "doc_id": "9f3c2e1a4b5c4d6e7f8090a1b2c3d4e5", "deleted": true }
```

Error: `401` (auth), `404` (`doc_id` tidak valid / tidak ada / expired).

## 10. File statis

- `GET /public/{doc_id}/doc.md` → markdown mentah.
- `GET /public/{doc_id}/images/<file>` → gambar hasil ekstraksi.

Tanpa auth (capability URL — `doc_id` tak tertebak). Prefix path relatif
(`/public/…`) dengan base URL service.
