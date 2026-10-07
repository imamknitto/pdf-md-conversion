# md-conversion-service-py

Standalone PDF→Markdown service (FastAPI). Consumed as a **remote REST API**
by `monorepo-docs-agent` (gateway) and any other program — this repo deploys
and versions independently.

> Default port is **8100**: `:8000` belongs to the public gateway in every
> environment. Auth: set `CONVERTER_API_KEY` (Bearer) in any shared/deployed
> environment; `/health` stays open. `/public/*` artifacts are capability URLs
> (unguessable `doc_id`); `/convert` + `/download` require the token when set.

REST API (FastAPI) + CLI untuk konversi PDF ke Markdown. Pipeline per halaman:

- **Teks**: PyMuPDF word-reflow (mengatasi PDF justify yang menulis satu kata per baris).
- **Tabel**: `pymupdf.find_tables()` + `pdfplumber.find_tables()`, divalidasi dan di-dedup (IoU).
- **Halaman scan**: RapidOCR (ONNX, pip-only) — opsional via `scan_images`.
- **Gambar**: hanya gambar bermakna; logo/header yang berulang tiap halaman dibuang.
- **markitdown**: fallback saat teks halaman kosong, dan siap untuk format non-PDF.

Artefak gambar disajikan statis dari `public/<doc_id>/` dan dibersihkan otomatis (TTL + batas ukuran).

## Struktur

```
app/
  main.py       FastAPI: POST /convert, mount /public, lifespan cleanup
  config.py     Konstanta & override via environment variable
  schemas.py    Model respons (Pydantic)
  extractor.py  Ekstraksi teks per halaman (PyMuPDF reflow)
  tables.py     Deteksi + validasi + render tabel Markdown
  ocr.py        Wrapper RapidOCR (lazy singleton)
  converter.py  Orkestrasi pipeline per halaman
  storage.py    doc_id, path public, sanitasi, tulis file
  cleanup.py    Sweeper TTL + batas disk
  cli.py        Entrypoint tanpa API
public/<doc_id>/{images,scans}
legacy/         Script lama (PyMuPDF manual) — arsip
tests/
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
| `--no-images` | Jangan ekstrak gambar |
| `--public-dir` | Override folder `public/` |
| `--json` | Cetak metadata JSON ke stdout |

## API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Endpoint (semua di bawah prefix deployment service ini):

- `GET /health` → `{"status":"ok","service":"md-conversion-service-py","version":"0.1.0"}` (open)
- `POST /convert` (multipart/form-data)
  - `file`: PDF, maksimal 30 MB
  - `scan_images`: bool (default `false`)
- `GET /download/{doc_id}` → file `text/markdown` (tanpa escape JSON)
- `GET /public/{doc_id}/doc.md` → markdown mentah (static)
- `GET /public/{doc_id}/images/<file>` → gambar hasil ekstraksi

Contoh:

```powershell
curl.exe -X POST "http://127.0.0.1:8100/convert" `
  -F "file=@brd-lms.pdf" -F "scan_images=false" -o resp.json
# dengan token: -H "Authorization: Bearer $env:CONVERTER_API_KEY"
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
> Di frontend pakai `resp.json()` (otomatis jadi newline asli), atau ambil
> file mentah via `markdown_url` / `/download/{doc_id}`. Contoh:
> `fetch(API + conv.markdown_url).then(r => r.text())`, lalu prefix
> `"/public/"` dengan base URL API sebelum feed ke markdown viewer
> (react-markdown + remark-gfm + rehype-sanitize).

Gambar dapat diakses di `http://127.0.0.1:8100/public/<doc_id>/images/<filename>`
(atau lewat gateway `:8000` dengan path yang sama).

## Docker

```powershell
Copy-Item .env.example .env   # wajib isi CONVERTER_API_KEY bila di-share/di-deploy
docker build -t md-conversion-service-py:0.1.0 .
docker run -d --name converter -p 127.0.0.1:8100:8100 --env-file .env md-conversion-service-py:0.1.0
curl.exe http://127.0.0.1:8100/health
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

## Batasan

- Tabel **borderless** (tanpa garis) sering tidak terdeteksi dan tampil sebagai teks biasa.
- RapidOCR mengunduh model saat pertama dijalankan (butuh internet sekali).
- Jalankan uvicorn dengan `--workers 1` (engine OCR singleton).
- v1 hanya mendukung PDF.

## Test manual

Lihat `## CLI` dan `## API` di atas. Untuk menguji pembersihan disk, jalankan dengan `PUBLIC_TTL_MINUTES=0` lalu restart server.
