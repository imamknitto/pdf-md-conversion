# pdf-md-conversion

Service REST API untuk konversi PDF ke Markdown (teks, tabel, gambar, OCR scan).
Dikonsumsi sebagai remote API oleh `apps/api`. Default port **8379**.

Converter hanya memproses PDF dan menyerahkan hasil melalui respons API:

- `markdown` berisi hasil Markdown.
- `images[].data_base64` berisi byte gambar/scan untuk disimpan consumer.
- URL `/public/...` di Markdown adalah URL relatif yang dipertahankan oleh
  consumer, bukan file yang dilayani converter.

Converter tidak menyimpan artefak konversi ke disk. Consumer
(`monorepo-docs-agent/apps/api`) menyimpan dan melayani file tersebut.
Folder `DATA_DIR` (default `./data`) menyimpan registry job async (`jobs.sqlite`).

> Auth: set `PDF_MD_CONVERTER_API_KEY` (Bearer) bila di-share/di-deploy;
> `/v1/health` selalu terbuka. Bila key di-set, semua endpoint lain butuh token.

## Cara run

Lokal:

```powershell
Copy-Item .env.example .env   # isi PDF_MD_CONVERTER_API_KEY bila perlu auth
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
.\dev.ps1   # http://127.0.0.1:8379
```

Docker:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
curl.exe http://127.0.0.1:8379/v1/health
```

## Kontrak API

Detail per endpoint (contoh request + respons): [`README_API_CONTRACT.md`](README_API_CONTRACT.md).

Base path `/v1`:

| Endpoint | Keterangan |
|---|---|
| `GET /v1/health` | Health check (tanpa auth) |
| `POST /v1/convert` | Konversi sinkron (PDF kecil/sedang) |
| `POST /v1/convert/jobs` | Konversi async → `202` (PDF besar) |
| `GET /v1/convert/jobs/{job_id}` | Status + progres + hasil job |
| `GET /v1/documents` | Legacy stub; selalu `{"documents": []}` |
| `GET /v1/download/{doc_id}` | Legacy stub; selalu `404` |
| `DELETE /v1/documents/{doc_id}` | Legacy stub; selalu `404` |

Contoh cepat:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/v1/convert" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" `
  -F "file=@dokumen.pdf" -o resp.json
```

Catatan:

- Rate limit: `POST /v1/convert` 10 req/menit, `POST /v1/convert/jobs` 20 req/menit (429 bila terlampaui).
- `PUBLIC_TTL_MINUTES` menentukan `expires_at` yang diserahkan ke consumer.
- Consumer wajib menyimpan `markdown` dan decode `images[].data_base64`.
- `data_base64` menambah ukuran response sekitar 33% dari byte gambar asli.
- Hanya mendukung PDF. Tabel tanpa garis (borderless) sering tidak terdeteksi. Untuk PDF 100+ halaman dengan `ocr_images=true`, pakai jalur async.
