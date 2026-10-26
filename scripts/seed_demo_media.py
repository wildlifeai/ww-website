#!/usr/bin/env python3
"""
seed_demo_media.py
==================
Populate the read-only demo project with the real sample camera-trap images in
``test-fixtures/camera-trap`` so the "Try the demo" account shows actual data
(it otherwise has a project but no images).

It uploads each fixture image to the public Supabase Storage bucket and creates
a ``media`` row on a deployment in the demo project. **Idempotent** — re-running
only adds images that aren't there yet, so it's safe to run on every change to
``test-fixtures`` (see .github/workflows/update-demo.yml) or after a dev DB reset.

Usage
-----
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/seed_demo_media.py
    # optional: --project-id <uuid> --fixtures <dir> --bucket <name> --limit N

The demo account (project_viewer) sees these via deployment -> project -> role,
so the deployment must live in the project the demo can view (default: the seeded
Wētāpunga project the demo is granted viewer on).
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from supabase import create_client
except ImportError:
    print("Missing dependency. Install with:  pip install supabase")
    sys.exit(1)

# Defaults match the dev seed (ww-backend/supabase/seeds/dev/data.sql):
#   demo viewer is granted on this project; a0…001 is the seed admin/owner.
DEMO_PROJECT_ID = "c0000000-0000-0000-0000-000000000001"
SEED_ADMIN_ID = "a0000000-0000-0000-0000-000000000001"
DEMO_BLUETOOTH_ID = "demo-cam-01"
DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "test-fixtures" / "camera-trap"
DEFAULT_BUCKET = "media-renditions"  # public bucket (renditions/CDN) — URLs are directly servable
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def ensure_deployment(svc, project_id: str) -> str:
    """Return a deployment id in the project, creating one (+ device) if needed."""
    existing = (
        svc.table("deployments").select("id").eq("project_id", project_id)
        .is_("deleted_at", "null").limit(1).execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    # Cameras belong to no organisation (ww-backend#346): find the demo camera by its id.
    dev = svc.table("devices").select("id").eq("bluetooth_id", DEMO_BLUETOOTH_ID).limit(1).execute()
    if dev.data:
        device_id = dev.data[0]["id"]
    else:
        device_id = (
            svc.table("devices").insert({
                "bluetooth_id": DEMO_BLUETOOTH_ID,
                "name": "Demo Camera", "modified_by": SEED_ADMIN_ID,
            }).execute().data[0]["id"]
        )

    created = svc.table("deployments").insert({
        "project_id": project_id, "device_id": device_id,
        "name": "Demo — Sample Camera Trap", "location_name": "Demo Reserve",
        "deployment_start": "2024-01-01T00:00:00Z", "setup_by": SEED_ADMIN_ID,
    }).execute()
    print(f"✓  Created demo deployment {created.data[0]['id']}")
    return created.data[0]["id"]


def main() -> None:
    p = argparse.ArgumentParser(description="Seed the demo project with sample images")
    p.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL", "http://localhost:54321"))
    p.add_argument("--service-role-key", default=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    # `or` (not getenv default) so an empty env var — common in CI when an
    # optional GitHub var is unset — falls back to the built-in default.
    p.add_argument("--project-id", default=os.getenv("DEMO_PROJECT_ID") or DEMO_PROJECT_ID)
    p.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p.add_argument("--bucket", default=os.getenv("SUPABASE_MEDIA_BUCKET") or DEFAULT_BUCKET)
    p.add_argument("--limit", type=int, default=0, help="Cap number of images (0 = all)")
    args = p.parse_args()

    if not args.service_role_key:
        print("✗  SUPABASE_SERVICE_ROLE_KEY is required")
        sys.exit(1)

    base = args.supabase_url.rstrip("/")
    svc = create_client(base + "/", args.service_role_key)
    print(f"🔌 Connected to {args.supabase_url}")

    images = sorted(
        f for f in Path(args.fixtures).rglob("*")
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
    )
    if args.limit:
        images = images[: args.limit]
    if not images:
        print(f"✗  No images found under {args.fixtures}")
        sys.exit(1)
    print(f"📁 {len(images)} fixture image(s)")

    deployment_id = ensure_deployment(svc, args.project_id)

    existing = svc.table("media").select("file_name").eq("deployment_id", deployment_id).execute()
    have = {r["file_name"] for r in (existing.data or [])}

    uploaded = skipped = 0
    for img in images:
        name = img.name
        if name in have:
            skipped += 1
            continue
        path = f"demo/{deployment_id}/{name}"
        content = img.read_bytes()
        # An already-existing object is fine (storage persists across DB resets);
        # any OTHER failure means the URL would be broken, so skip the media row.
        upload_ok = True
        try:
            svc.storage.from_(args.bucket).upload(
                path, content, file_options={"content-type": "image/jpeg"},
            )
        except Exception as exc:
            if "exists" not in str(exc).lower():
                print(f"⚠  upload failed for {name}: {exc}")
                upload_ok = False
        if not upload_ok:
            continue
        public_url = f"{base}/storage/v1/object/public/{args.bucket}/{path}"
        svc.table("media").insert({
            "deployment_id": deployment_id, "file_path": public_url,
            "file_name": name, "file_mediatype": "image/jpeg", "uploaded_by": SEED_ADMIN_ID,
        }).execute()
        uploaded += 1

    print(f"\n✅ Demo media seeded: {uploaded} added, {skipped} already present "
          f"(deployment {deployment_id}).")


if __name__ == "__main__":
    main()
