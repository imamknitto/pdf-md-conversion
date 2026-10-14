"""Regenerasi snapshot openapi.json dari kode saat ini.

Usage: python scripts/generate_openapi.py [--check]
  default : tulis openapi.json
  --check : exit 1 bila snapshot beda dengan kode (untuk CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.main import app

    snapshot = ROOT / "openapi.json"
    live = app.openapi()
    if "--check" in sys.argv[1:]:
        committed = json.loads(snapshot.read_text(encoding="utf-8"))
        if committed != live:
            print("openapi.json beda dengan kode saat ini; jalankan scripts/generate_openapi.py")
            return 1
        print("openapi.json sinkron.")
        return 0
    snapshot.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
    print(f"ditulis: {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
