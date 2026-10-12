# Changelog

## Unreleased

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
