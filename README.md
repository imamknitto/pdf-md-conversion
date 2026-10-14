# pdf-md-conversion

Service REST API untuk konversi PDF ke Markdown (teks, tabel, gambar, OCR scan).
Dikonsumsi sebagai remote API oleh aplikasi apapun. Default port **8379**.

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

Base path `/v1` (kecuali `/public/*`):

| Endpoint | Keterangan |
|---|---|
| `GET /v1/health` | Health check (tanpa auth) |
| `POST /v1/convert` | Konversi sinkron (PDF kecil/sedang) |
| `POST /v1/convert/jobs` | Konversi async → `202` (PDF besar) |
| `GET /v1/convert/jobs/{job_id}` | Status + progres + hasil job |
| `GET /v1/documents` | Daftar dokumen yang masih tersedia |
| `GET /v1/download/{doc_id}` | File markdown mentah |
| `DELETE /v1/documents/{doc_id}` | Hapus dokumen + artefaknya |

Contoh cepat:

```powershell
curl.exe -X POST "http://127.0.0.1:8379/v1/convert" `
  -H "Authorization: Bearer $env:PDF_MD_CONVERTER_API_KEY" `
  -F "file=@dokumen.pdf" -o resp.json
```

Catatan:

- Rate limit: `POST /v1/convert` 10 req/menit, `POST /v1/convert/jobs` 20 req/menit (429 bila terlampaui).
- Dokumen kedaluwarsa otomatis (default 60 menit).
- Hanya mendukung PDF. Tabel tanpa garis (borderless) sering tidak terdeteksi. Untuk PDF 100+ halaman dengan `ocr_images=true`, pakai jalur async.
