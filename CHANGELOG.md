# Changelog

## Unreleased

- **Breaking**: hapus endpoint legacy `GET /v1/documents`,
  `GET /v1/download/{doc_id}`, dan `DELETE /v1/documents/{doc_id}` (selalu
  kosong/404). Router, test, dan entri dokumentasi/openapi ikut dihapus.
- Async job kini memakai satu worker thread dengan antrean nyata
  (`queue.Queue`): job kedua tetap `queued` sampai job pertama selesai,
  `CONVERT_MAX_QUEUE` menjadi batas kedalaman antrean, dan worker berhenti
  dengan rapi saat shutdown (job aktif diselesaikan).
- Upload PDF di-stream per chunk ke file sementara (tidak lagi dibaca penuh
  ke memori); validasi ukuran/magic bytes/jumlah halaman dari file, dan file
  sementara selalu dihapus setelah sync/async job selesai.
- SQLite: `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`;
  sweep TTL maksimal sekali per 60 detik; update progres di-throttle
  (per 5% / per detik / saat selesai); webhook memakai record job yang sudah
  ada (tidak query ulang).
- Kurangi pekerjaan duplikat: daftar gambar tiap halaman diambil sekali per
  dokumen untuk recurring-xref dan analisis; deteksi flowchart per halaman
  yang hasilnya tidak pernah masuk markdown dihapus; pdfplumber dibuka
  lazily; halaman scan OCR langsung dari pixmap tanpa encode/decode PNG.
- `GZipMiddleware` (minimum 1024 byte) untuk respons besar.
- Satu instance rate limiter di `app.core.rate_limit`; key token API di-hash
  SHA-256; limiter ganda per-route dihapus.
- Test: sample PDF dibuat programatik (tidak butuh `sample-doc/*.pdf`), test
  antrean/graceful shutdown/SQLite concurrency/TTL, dan workflow CI
  (`pytest`, `ruff`, `generate_openapi.py --check`).

- Fix job lifecycle: error non-cancel kini selalu jadi status `error` (tidak
  ada job stuck `processing`); job `queued`/`processing` yang tertinggal saat
  restart ditandai `error` lewat recovery startup; cancel tetap idempoten dan
  status `cancelled` tidak ditimpa.
- Webhook: `httpx` dipindah ke dependency utama (jalan di image produksi),
  pengiriman pindah ke thread terpisah (tidak memblokir event loop), retry
  dibatasi 3 percobaan. SSRF guard menolak host privat/loopback/link-local,
  kredensial URL, port non-standar, dan redirect.
- Resource limits: `MAX_PAGES=500` dan `MAX_IMAGE_PIXELS=50000000`; PDF/gambar
  di atas batas ditolak `400`/`413`. Cap `MAX_IMAGES_PER_DOC` kini dihitung
  per dokumen (sebelumnya per halaman).
- Tabel: karakter `|` di sel di-escape menjadi `\|` agar markdown valid.
- PDF berpassword dicek dengan context manager (tidak ada file handle bocor).
- 429 mengembalikan `{"detail": ...}` sesuai kontrak; perbandingan token
  memakai `secrets.compare_digest`.
- `data_base64` hanya dikirim saat opsi gambar/scan aktif; `images` kosong
  bila tidak diminta.
- Config: `.env` dibaca otomatis, default `CONVERT_MAX_QUEUE=4`, satu sumber
  default, versi diambil dari metadata package. Hapus `PUBLIC_DIR`.
- Hapus dead code: `unfinished_count`, `get_circuit_state`,
  `reset_circuit_breaker`, parameter `_filename` dan `page` yang tidak dipakai.

- `POST /v1/ocr`: OCR sinkron gambar standalone (PNG/JPEG/WebP/TIFF). Respons
  memakai `ConvertResponse` yang sama dengan `/v1/convert`. CLI menerima input
  gambar dengan suffix yang didukung. Batas ukuran `MAX_IMAGE_MB`.
- Rename identitas `doc-md-conversion` → `pdf-md-conversion` (**breaking**):
  package + CLI (`pdf-md-conversion`), FastAPI title, logger
  `pdf-md-conversion.*`, `/health.service`, env `CONVERTER_API_KEY` →
  `PDF_MD_CONVERTER_API_KEY`, image Docker, service/container/volume compose.
  Konsumen/deploy lama wajib menyesuaikan token env + cek `service`.
- Fix Docker: tambah `libglib2.0-0t` (opencv-python via RapidOCR gagal import
  tanpa ini → semua OCR `broken` + circuit breaker terbuka), HEALTHCHECK ke
  `/v1/health`, dan exception engine OCR kini masuk log.

- Optimasi CPU konversi (tanpa mengubah kontrak API/markdown): metadata gambar
  tiap halaman (`get_images`/`get_image_rects`) dan `get_text("words")` dihitung
  sekali per halaman lalu dipakai ulang lintas stage; `get_drawings()` halaman
  di-cache dan dibagi ke `is_flowchart()` + `from_page()` (param `drawings`/
  `words` opsional). Config baru `TABLE_FAST_MODE` (default `false` = perilaku
  sekarang): bila `true`, deteksi tabel pdfplumber dilewati untuk halaman yang
  sudah punya tabel valid dari PyMuPDF.
- Rate limit `POST /convert/jobs` dinaikkan `5/minute` → `20/minute`, dan
  default `CONVERT_MAX_QUEUE` `4` → `8` — menyertai platform yang kini
  selalu memakai jalur async untuk semua konversi.
- Fix: ekstraksi gambar embedded memakai `Document.extract_image` (via
  `page.parent`) — sebelumnya `Page.extract_image` yang tidak ada, sehingga
  semua gambar ter-skip diam-diam dan OCR gambar tidak pernah jalan.
- Refaktor struktur berlayer (`app/core`, `app/api`, `app/schemas`,
  `app/services`, `app/converters`, `app/storage`) tanpa mengubah kontrak
  eksternal: path endpoint, field form, bentuk respons, status code, auth,
  nama env var, opsi CLI, dan isi markdown hasil konversi tetap sama.
- `config.py` kini `pydantic-settings` `BaseSettings` (nama env, default,
  tipe sama). Nilai env tidak valid tetap jatuh kembali ke default seperti
  perilaku sebelumnya. Dependensi baru: `pydantic-settings`.
- `tests/` (pytest + httpx TestClient): test karakterisasi `/health`, auth,
  `/convert`, `/convert/jobs` + polling, `/documents`, `/download`,
  `DELETE /documents/{doc_id}`.
- `scripts/generate_openapi.py` untuk regenerasi snapshot `openapi.json`
  (opsi `--check`).
- `pyproject.toml`: config `ruff` (E/F/I/UP) + `pytest`, dev deps
  (`httpx`, `ruff`).

- Async conversion: `POST /convert/jobs` (field sama seperti `/convert`) →
  `202 {job_id, doc_id}` + `GET /convert/jobs/{job_id}` (status
  `queued|processing|done|error`, progres per halaman, `result`
  `ConvertResponse` penuh). Antrean dibatasi `CONVERT_MAX_QUEUE` (429 bila
  penuh), status kedaluwarsa `CONVERT_JOB_TTL_MINUTES`. Jalan di
  `BackgroundTasks` + thread agar `/health` tetap responsif di `--workers 1`.
- OCR cost guards (CPU ~3–4 dtk/gambar full-res): `prepare_image()`
  downscale ke `OCR_MAX_SIDE`, lewati gambar < `OCR_SKIP_BELOW` px, cap
  `OCR_MAX_IMAGES` gambar di-OCR per dokumen (sisanya disimpan tanpa teks
  OCR + warning). Config: `OCR_MAX_SIDE`, `OCR_SKIP_BELOW`, `OCR_MAX_IMAGES`.
- Sync `/convert`: log start/done (nama file, ukuran, halaman).

- `/convert` + CLI: param/flag baru `ocr_images` (`--ocr-images`) — OCR tiap
  gambar embedded (dengan upscale aman LANCZOS + urutan baca) + Mermaid
  flowchart; `ImageMeta` gains `ocr_text` + `flowchart_mermaid`.
  Mermaid TIDAK masuk markdown (hanya field API `flowchart_mermaid`);
  markdown hanya memuat blok `> OCR:` di bawah link gambarnya.
- `/convert` + CLI: `ocr_scope` (`all`|`flowchart`, `--ocr-scope`) — mode
  `flowchart` hanya menulis blok OCR untuk gambar yang lolos
  `flowchart.is_flowchart()` (langkah bernomor, kata alur, label Y/N,
  shape vektor; ambang `FLOWCHART_SCORE_MIN`); sisanya link saja +
  warning per halaman. 400 untuk nilai lain.
- `is_flowchart()` diperketat lawan false-positive screenshot ber-badge
  angka: langkah bernomor wajib berisi teks (≥3 karakter), dan skor saja
  tidak cukup — wajib ada ≥1 sinyal kuat (nomor/kata alur/label Y-N/
  shape vektor).
- Flowchart vektor lokal (tanpa LLM): `app/flowchart.py` merekonstruksi
  node/edge dari `get_drawings()` + posisi kata dan meng-emit Mermaid
  (`vector-graph`, fallback jujur `reading-order` + warning bila tak pasti).
- `app/ocr.py`: `ocr_image()` kini upscale aman (cap `OCR_MIN_SIDE` /
  `OCR_MAX_SCALE` / `OCR_MAX_PIXELS`) + `ocr_image_boxes()` berposisi.
- Config baru: `OCR_MIN_SIDE`, `OCR_MAX_SCALE`, `OCR_MAX_PIXELS`,
  `FLOWCHART_ENABLE`, `FLOWCHART_Y_TOL`, `FLOWCHART_MAX_SHAPES`.

- Rename identitas ke `doc-md-conversion` (standalone, tanpa referensi
  konsumen): package, CLI `doc-md-conversion`, FastAPI title +
  `/health.service` + logger `doc-md-conversion.*`, image Docker,
  judul README. Default port tetap `8379`.
- Hapus `legacy/` (script lama sudah digantikan pipeline `app/` + CLI).
- Cabut fallback `markitdown`: dependensi + `_markitdown_page()` dihapus.
  Halaman tanpa text layer yang bukan scan kini dibiarkan kosong.
  `numpy` + `pydantic` dideklarasikan eksplisit (sebelumnya transitif).
- `openapi.json` snapshot diregenerasi (title baru).

- `DELETE /documents/{doc_id}` — delete a converted document and all its
  artifacts (`doc.md`, `meta.json`, `images/`, `scans/`). 404 when the
  doc_id is invalid or already gone.
- `/convert` gains an independent `extract_images` form field (default
  `true`). `scan_images` now controls OCR of scanned pages only; set
  `extract_images=false` for text-only output.
- `GET /documents` — list available converted documents (newest first,
  with `meta.json` stats when present, mtime fallback otherwise).
- `/convert` now writes `meta.json` beside `doc.md` (filename, stats,
  warnings, created/expires timestamps).
- `openapi.json` snapshot includes `/documents` and `/documents/{doc_id}`.

## 0.1.0 — split from monorepo

- Standalone repo: `app/`, `Dockerfile`, `pyproject.toml`, docs.
- Default port `8100` (`:8000` belongs to the public gateway everywhere).
- `GET /health` returns `{status, service, version}`.
- Optional bearer auth (`CONVERTER_API_KEY`) on `/convert` + `/download`.
  Empty key = open (local dev only). `/health` stays open.
- `openapi.json` snapshot committed for TS consumers.
