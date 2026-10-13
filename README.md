# doc-md-conversion

Standalone PDF→Markdown service (FastAPI) — deploy dan versioning mandiri,
dapat dikonsumsi sebagai **remote REST API** oleh aplikasi apapun.

> Default port **8379**. Auth: set `CONVERTER_API_KEY` (Bearer) di
> environment yang di-share/di-deploy; `/health` stays open. Artefak
> `/public/*` adalah capability URL (`doc_id` tak tertebak); `/convert` +
> `/convert/jobs` + `/download` + `/documents` + `DELETE /documents/{doc_id}`
> butuh token bila key di-set.

REST API (FastAPI) + CLI untuk konversi PDF ke Markdown. Pipeline per halaman:

- **Teks**: PyMuPDF word-reflow (mengatasi PDF justify yang menulis satu kata per baris).
- **Tabel**: `pymupdf.find_tables()` + `pdfplumber.find_tables()`, divalidasi dan di-dedup (IoU).
- **Halaman scan**: RapidOCR (ONNX, pip-only) — opsional via `scan_images`.
- **Gambar**: hanya gambar bermakna; logo/header yang berulang tiap halaman dibuang.

Artefak gambar disajikan statis dari `public/<doc_id>/` dan dibersihkan otomatis (TTL + batas ukuran).

## Struktur

```
app/
  main.py               FastAPI app, lifespan cleanup, CORS, mount /public, include router
  core/
    config.py           Settings (pydantic-settings) + override via environment variable
    security.py         Bearer auth (CONVERTER_API_KEY, aktif hanya bila di-set)
  api/
    deps.py             auth, validasi upload, tulis meta + respons
    routes/
      health.py         GET /health
      convert.py        POST /convert (sinkron)
      jobs.py           POST /convert/jobs + GET /convert/jobs/{job_id}
      documents.py      GET /documents, GET /download/{doc_id}, DELETE /documents/{doc_id}
  schemas/              Model request/respons (Pydantic)
  services/
    conversion_service.py  Orkestrasi pipeline per halaman
    job_service.py         Registry in-memory untuk job konversi async
  converters/
    extractor.py        Ekstraksi teks per halaman (PyMuPDF reflow)
    tables.py           Deteksi + validasi + render tabel Markdown
    ocr.py              Wrapper RapidOCR (lazy singleton) + cost guards
    flowchart.py        Rekonstruksi flowchart vektor lokal → Mermaid (tanpa LLM)
  storage/
    storage.py          doc_id, path public, sanitasi, tulis file
    cleanup.py          Sweeper TTL + batas disk
  cli.py                Entrypoint tanpa API
public/<doc_id>/{images,scans}
tests/                  Test karakterisasi kontrak API (pytest + httpx)
scripts/generate_openapi.py  Regenerasi snapshot openapi.json
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## CLI (tanpa API)

```powershell
.\.venv\Scripts\python.exe -m app.cli .\brd-lms.pdf -o .\out.md
.\.venv\Scripts\python.exe -m app.cli .\brd-lms.pdf --scan-images --json
.\.venv\Scripts\python.exe -m app.cli .\brd-lms.pdf --no-images
```

Opsi:

| Opsi | Keterangan |
|---|---|
| `-o, --output` | Path file `.md` keluaran (default: samping input) |
| `--scan-images` | Aktifkan OCR halaman scan |
| `--ocr-images` | OCR tiap gambar embedded + flowchart |
| `--ocr-scope` | `all` (default) atau `flowchart` |
| `--no-images` | Jangan ekstrak gambar |
| `--public-dir` | Override folder `public/` |
| `--json` | Cetak metadata JSON ke stdout |

## API

```powershell
.\dev.ps1   # default http://127.0.0.1:8379 (baca .env bila ada)
```

Manual (tanpa script):

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8379 --workers 1
```

Endpoint (semua di bawah prefix deployment service ini):

- `GET /health` → `{"status":"ok","service":"doc-md-conversion","version":"0.1.0"}` (open)
- `POST /convert` (multipart/form-data, sinkron — untuk PDF kecil/sedang)
  - `file`: PDF, maksimal 30 MB
  - `scan_images`: bool (default `false`) — OCR halaman scan
  - `extract_images`: bool (default `true`) — ekstrak gambar embedded + halaman scan
  - `ocr_images`: bool (default `false`) — OCR tiap gambar embedded (upscale
    aman + urutan baca) langsung di bawah link gambarnya sebagai blok `> OCR:`;
    Mermaid hanya ada di field API `flowchart_mermaid`, tidak masuk markdown.
    Butuh `extract_images=true` agar berefek
  - `ocr_scope`: `all` (default) atau `flowchart` — bila `flowchart`, blok OCR
    hanya ditulis untuk gambar yang terdeteksi sebagai diagram/flowchart
    (sinyal teks + bentuk vektor, ambang `FLOWCHART_SCORE_MIN`); sisanya tetap
    sebagai link saja. Diabaikan bila `ocr_images=false`
- `POST /convert/jobs` (multipart/form-data, field sama seperti `/convert`)
  → `202 {"job_id","doc_id","status":"queued"}` — untuk PDF besar; 429 bila
  antrean penuh (`CONVERT_MAX_QUEUE`)
- `GET /convert/jobs/{job_id}` → `{"job_id","doc_id","filename","status",
  "progress":{"pages_done","pages_total"},"result","error"}` dengan
  `status`: `queued` | `processing` | `done` | `error`; `result` berisi
  `ConvertResponse` penuh saat done; 404 bila job tak dikenal/expired
- `GET /documents` → daftar dokumen yang masih tersedia (terbaru dulu)
- `GET /download/{doc_id}` → file `text/markdown` (tanpa escape JSON)
- `DELETE /documents/{doc_id}` → hapus dokumen + semua artefaknya (`{"doc_id","deleted":true}`, 404 bila tak ada)
- `GET /public/{doc_id}/doc.md` → markdown mentah (static)
- `GET /public/{doc_id}/images/<file>` → gambar hasil ekstraksi

Contoh:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/convert" `
  -F "file=@brd-lms.pdf" -F "scan_images=false" -o resp.json
# dengan token: -H "Authorization: Bearer $env:CONVERTER_API_KEY"

# async untuk PDF besar (202 lalu poll):
curl.exe -X POST "http://127.0.0.1:8379/convert/jobs" `
  -F "file=@besar.pdf" -F "ocr_images=true" -o job.json
curl.exe "http://127.0.0.1:8379/convert/jobs/<job_id>"
```

Respons JSON:

```json
{
  "doc_id": "…",
  "markdown": "## Page 1\n\n…",
  "markdown_url": "/public/<doc_id>/doc.md",
  "stats": { "pages": 101, "scanned_pages": 0, "tables": 4, "images_kept": 104, "images_skipped": 102 },
  "images": [ { "page": 40, "kind": "image", "filename": "page-040-image-001.jpeg",
                "url": "/public/<doc_id>/images/page-040-image-001.jpeg",
                "width": 1268, "height": 658, "size_bytes": 63488 } ],
  "warnings": [],
  "expires_at": "2026-10-02T13:00:00+00:00"
}
```

> `markdown` di JSON selalu ter-escape (`\n`) — itu normal JSON, bukan bug.
> Di konsumen pakai JSON parse (otomatis jadi newline asli), atau ambil
> file mentah via `markdown_url` / `/download/{doc_id}` lalu prefix
> `"/public/"` dengan base URL service ini.

Gambar dapat diakses di `http://127.0.0.1:8379/public/<doc_id>/images/<filename>`.

## Rate Limiting

API dilindungi rate limiting berbasis IP atau API key:

| Endpoint | Limit | Key |
|---|---|---|
| `POST /convert` | 10 req/menit | API key (jika auth) / IP |
| `POST /convert/jobs` | 20 req/menit | API key (jika auth) / IP |

Response `429 Too Many Requests` dikembalikan saat limit terlampaui.

## Reverse Proxy (nginx) — Wajib di Production

Service ini berjalan single-worker dan tidak handle TLS/buffering besar. **Selalu** letakkan di belakang nginx:

```nginx
# /etc/nginx/sites-available/doc-md-conversion
server {
    listen 8379;
    server_name converter.example.com;

    # Batasi ukuran body di layer proxy (tambahan dari MAX_UPLOAD_MB)
    client_max_body_size 35M;

    # Proxy ke service
    location / {
        proxy_pass http://127.0.0.1:8379;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;    # PDF besar butuh waktu lama
        proxy_send_timeout 300s;
        proxy_request_buffering off; # Streaming upload besar
    }

    # Health check tanpa auth
    location /health {
        proxy_pass http://127.0.0.1:8379/health;
        access_log off;
    }

    # Static files (public artifacts) — optional: serve langsung nginx untuk performa
    location /public/ {
        alias /data/public/;  # mount volume PUBLIC_DIR
        expires 1h;
        add_header Cache-Control "public";
    }
}
```

> **Penting**: `client_max_body_size` di nginx **harus** >= `MAX_UPLOAD_MB` + margin. Default 30MB → set 35M.

## Docker

```powershell
Copy-Item .env.example .env   # wajib isi CONVERTER_API_KEY bila di-share/di-deploy
docker build -t doc-md-conversion:0.1.0 .
docker run -d --name converter -p 127.0.0.1:8379:8379 --env-file .env doc-md-conversion:0.1.0
curl.exe http://127.0.0.1:8379/health
```

Catatan: image run sebagai non-root, 1 worker (OCR singleton),
`PUBLIC_DIR=/data/public` di-mount ke volume bila artefak perlu persist.
Model RapidOCR diunduh saat request OCR pertama (butuh internet sekali).

## Konfigurasi (environment variable)

| Variabel | Default | Keterangan |
|---|---|---|
| `CONVERTER_API_KEY` | *(kosong = terbuka)* | Bearer token untuk `/convert` + `/download`; wajib diisi bila di-share/di-deploy |
| `MAX_UPLOAD_MB` | `30` | Batas ukuran upload |
| `PUBLIC_DIR` | `./public` | Folder artefak statis |
| `PUBLIC_TTL_MINUTES` | `60` | Umur folder sebelum dihapus |
| `PUBLIC_MAX_MB` | `1024` | Batas total ukuran `public/` |
| `CLEANUP_INTERVAL_MINUTES` | `10` | Interval sweeper |
| `MIN_TEXT_CHARS` | `50` | Ambang deteksi halaman scan |
| `PAGE_COVERAGE` | `0.5` | Rasio gambar menutupi halaman agar dianggap scan |
| `RECURRING_XREF_RATIO` | `0.5` | Rasio kemunculan gambar untuk dianggap logo/header |
| `IMAGE_MIN_AREA_RATIO` | `0.01` | Luas minimum gambar agar disimpan |
| `MAX_IMAGE_MB` | `10` | Batas ukuran satu gambar |
| `MAX_IMAGES_PER_DOC` | `200` | Batas jumlah gambar per dokumen |
| `OCR_DPI` | `200` | Resolusi render untuk OCR |
| `OCR_MAX_SIDE` | `1600` | Gambar OCR di-downscale ke sisi terpanjang ini (hemat CPU) |
| `OCR_SKIP_BELOW` | `100` | Gambar lebih kecil dari ini (px) dilewati OCR |
| `OCR_MAX_IMAGES` | `100` | Maks gambar di-OCR per dokumen; sisa disimpan tanpa teks OCR |
| `CONVERT_JOB_TTL_MINUTES` | `120` | Umur status job async (`POST /convert/jobs`) |
| `CONVERT_MAX_QUEUE` | `8` | Maks job async belum selesai; selebihnya 429 |

## Batasan

- Tabel **borderless** (tanpa garis) sering tidak terdeteksi dan tampil sebagai teks biasa.
- Halaman tanpa text layer yang bukan halaman scan dibiarkan kosong di output.
- RapidOCR mengunduh model saat pertama dijalankan (butuh internet sekali).
- OCR itu CPU-bound (~3–4 dtk per gambar full-res): PDF 100+ halaman dengan
  `ocr_images=true` bisa memakan waktu puluhan menit dalam mode sinkron —
  pakai `POST /convert/jobs` + cost guards (`OCR_MAX_SIDE` /
  `OCR_SKIP_BELOW` / `OCR_MAX_IMAGES`, gambar di atas cap tetap disimpan
  tanpa teks OCR + warning).
- Jalankan uvicorn dengan `--workers 1` (engine OCR singleton).
- v1 hanya mendukung PDF.

## Test manual

Lihat `## CLI` dan `## API` di atas. Untuk menguji pembersihan disk, jalankan dengan `PUBLIC_TTL_MINUTES=0` lalu restart server.
