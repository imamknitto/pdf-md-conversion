# Changelog

## 0.1.0 — split from monorepo

- Standalone repo: `app/`, `Dockerfile`, `pyproject.toml`, docs.
- Default port `8100` (`:8000` belongs to the public gateway everywhere).
- `GET /health` returns `{status, service, version}`.
- Optional bearer auth (`CONVERTER_API_KEY`) on `/convert` + `/download`.
  Empty key = open (local dev only). `/health` stays open.
- `openapi.json` snapshot committed for TS consumers.
