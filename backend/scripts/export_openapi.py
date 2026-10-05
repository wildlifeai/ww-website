"""Write the API's OpenAPI schema to backend/openapi.json, or check it is current.

The frontend calls this API by hand-written paths and types, so a renamed
field or a dropped endpoint used to reach it only when a page broke. The
committed snapshot makes an API change visible in the pull request that
makes it: CI runs `--check`, which fails with a diff when the schema the
app generates differs from the file, and the fix is to run this script and
commit the result alongside the change (#218).

    python scripts/export_openapi.py           # rewrite backend/openapi.json
    python scripts/export_openapi.py --check   # exit 1 with a diff if stale

Needs the same env as the tests (SUPABASE_URL, SUPABASE_ANON_KEY,
SUPABASE_SERVICE_ROLE_KEY), because importing the app validates its config.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "openapi.json"


def render() -> str:
    sys.path.insert(0, str(ROOT))
    from app.main import app  # noqa: PLC0415  (import after sys.path, by design)

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    current = render()
    if "--check" not in sys.argv:
        SNAPSHOT.write_text(current)
        print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(current)} bytes)")
        return 0
    on_disk = SNAPSHOT.read_text() if SNAPSHOT.exists() else ""
    if on_disk == current:
        print("openapi.json is current")
        return 0
    diff = difflib.unified_diff(on_disk.splitlines(), current.splitlines(), "openapi.json (committed)", "openapi.json (generated)", lineterm="")
    print("\n".join(diff))
    print("\nopenapi.json is stale: run `python scripts/export_openapi.py` in backend/ and commit the result.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
