#!/usr/bin/env python3
"""
seed_demo_media.py
==================
Populate the read-only demo project with the real sample camera-trap images in
``test-fixtures/camera-trap``, and their labels, so the "Try the demo" account shows
actual data (it otherwise has a project but no images).

It uploads each fixture image to the public Supabase Storage bucket and creates a
``media`` row on a deployment in the demo project, dated from the photo's EXIF
``DateTimeOriginal`` as an upload dates it. Then it gives each photo the label in
``test-fixtures/camera-trap/demo-labels.csv`` as an ``observations`` row (``source_type``
'imported', ``classified_by`` :data:`LABELLED_BY`), as a CamtrapDP import writes a
labelled package's rows.

**Idempotent**: re-running only adds images that aren't there yet, dates rows that have no
capture time, and writes a label only where the photo's row is missing or no longer says
what the file says. It touches no observation it did not write, so it is safe to run on
every change to ``test-fixtures`` (see .github/workflows/update-demo.yml) or after a dev DB
reset.

Usage
-----
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/seed_demo_media.py
    # optional: --project-id <uuid> --fixtures <dir> --labels <csv> --bucket <name> --limit N

Needs ``supabase`` and ``structlog`` (the EXIF parser is the backend's,
``backend/app/domain/exif.py``).

The demo account (project_viewer) sees these via deployment -> project -> role,
so the deployment must live in the project the demo can view (default: the seeded
Wētāpunga project the demo is granted viewer on).
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from supabase import create_client

    from app.domain.exif import parse_exif_from_bytes
except ImportError as exc:
    print(f"Missing dependency ({exc.name}). Install with:  pip install supabase structlog")
    sys.exit(1)

# Defaults match the dev seed (ww-backend/supabase/seeds/dev/data.sql):
#   demo viewer is granted on this project; a0…001 is the seed admin/owner.
DEMO_PROJECT_ID = "c0000000-0000-0000-0000-000000000001"
SEED_ADMIN_ID = "a0000000-0000-0000-0000-000000000001"
DEMO_BLUETOOTH_ID = "demo-cam-01"
DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "test-fixtures" / "camera-trap"
DEFAULT_LABELS = DEFAULT_FIXTURES / "demo-labels.csv"
DEFAULT_BUCKET = "media-renditions"  # public bucket (renditions/CDN) — URLs are directly servable
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# observations.classified_by on every label this script writes. With source_type 'imported' it
# is how a re-run finds its own rows, and the only rows it ever replaces or removes.
LABELLED_BY = "Wildlife Watcher demo labels"
LABEL_FIELDS = ("observation_type", "scientific_name", "vernacular_name")
OBSERVATION_TYPES = {"animal", "human", "vehicle", "blank", "unknown"}  # ww-backend's CHECK
PAGE = 1000  # PostgREST returns at most this many rows per request
CHUNK = 100


def read_all(build) -> list[dict]:
    """Every row of the read ``build()`` makes, a page at a time; ``build`` must order the rows."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = build().range(offset, offset + PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def capture_time(content: bytes) -> str | None:
    """The photo's EXIF DateTimeOriginal as ISO, as an upload stores ``media.timestamp``; None without one."""
    raw = parse_exif_from_bytes(content).get("Datetime_Original")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def read_labels(path: Path) -> dict[str, dict]:
    """The label file as {file name: observation fields}. The file name is the key ``media.file_name`` holds."""
    labels: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            name = Path(row["path"]).name
            label = {k: (row[k] or "").strip() or None for k in LABEL_FIELDS}
            if label["observation_type"] not in OBSERVATION_TYPES:
                raise ValueError(f"{path.name} line {line}: unknown observation_type {label['observation_type']!r}")
            if label["observation_type"] != "animal" and (label["scientific_name"] or label["vernacular_name"]):
                raise ValueError(f"{path.name} line {line}: only an animal carries a species name")
            if name in labels:
                raise ValueError(f"{path.name} line {line}: {name} is labelled twice")
            labels[name] = label
    return labels


def label_row(deployment_id: str, media_id: str, label: dict, now: str) -> dict:
    """One photo's label as an ``observations`` insert, None values dropped.

    Provenance is a CamtrapDP import's for an unprocessed package with no classificationMethod
    (``domain/camtrapdp.py``): imported, unreviewed. The labels were set by eye, not reviewed by a
    person, so the demo shows them as needing review. No box: the label is for the photo.
    """
    row = {
        "deployment_id": deployment_id,
        "media_id": media_id,
        "observation_level": "media",
        **label,
        "source_type": "imported",
        "review_status": "unreviewed",
        "classified_by": LABELLED_BY,
        "classification_timestamp": now,
    }
    return {k: v for k, v in row.items() if v is not None}


def seed_labels(svc, deployment_id: str, media: list[dict], labels: dict[str, dict]) -> tuple[int, int, int]:
    """Give each labelled photo its row from the label file; returns (written, already right, removed).

    Reads and replaces only this script's rows (source_type 'imported', classified_by
    :data:`LABELLED_BY`), so an AI run's rows or a person's label on a demo photo are never
    touched. A photo whose one row already says what the file says is left alone; a photo whose
    label changed gets a new row, and a row whose photo the file no longer labels is removed.
    """
    wanted = {m["id"]: labels[m["file_name"]] for m in media if m.get("file_name") in labels}
    ours = read_all(
        lambda: (
            svc.table("observations")
            .select("id, media_id, " + ", ".join(LABEL_FIELDS))
            .eq("deployment_id", deployment_id)
            .eq("source_type", "imported")
            .eq("classified_by", LABELLED_BY)
            .is_("deleted_at", "null")
            .order("id")
        )
    )
    by_media: dict[str | None, list[dict]] = {}
    for r in ours:
        by_media.setdefault(r.get("media_id"), []).append(r)

    removed = [r["id"] for media_id, rows in by_media.items() if media_id not in wanted for r in rows]
    replaced: list[str] = []
    inserts: list[dict] = []
    kept = 0
    now = datetime.now(timezone.utc).isoformat()
    for media_id, label in wanted.items():
        rows = by_media.get(media_id, [])
        if len(rows) == 1 and all(rows[0].get(k) == label[k] for k in LABEL_FIELDS):
            kept += 1
            continue
        replaced += [r["id"] for r in rows]
        inserts.append(label_row(deployment_id, media_id, label, now))

    # New rows first: a run that stops part way leaves a photo with an old label, never none,
    # and the next run finds the extra row and replaces both.
    for i in range(0, len(inserts), CHUNK):
        svc.table("observations").insert(inserts[i : i + CHUNK]).execute()
    stale = removed + replaced
    for i in range(0, len(stale), CHUNK):
        svc.table("observations").delete().in_("id", stale[i : i + CHUNK]).execute()
    return len(inserts), kept, len(removed)


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


def seed(svc, base: str, project_id: str, images: list[Path], bucket: str, labels: dict[str, dict]) -> None:
    """Upload and register the images on the demo deployment, date them, then label them."""
    deployment_id = ensure_deployment(svc, project_id)

    def media_rows():
        return svc.table("media").select("id, file_name, timestamp").eq("deployment_id", deployment_id).order("id")

    have = {r["file_name"]: r for r in read_all(media_rows)}

    uploaded = skipped = dated = 0
    for img in images:
        name = img.name
        content = img.read_bytes()
        taken = capture_time(content)
        row = have.get(name)
        if row:
            skipped += 1
            # Rows seeded before the script dated them: Insights counts a photo per capture day.
            if taken and not row.get("timestamp"):
                svc.table("media").update({"timestamp": taken}).eq("id", row["id"]).execute()
                dated += 1
            continue
        path = f"demo/{deployment_id}/{name}"
        # An already-existing object is fine (storage persists across DB resets);
        # any OTHER failure means the URL would be broken, so skip the media row.
        upload_ok = True
        try:
            svc.storage.from_(bucket).upload(
                path, content, file_options={"content-type": "image/jpeg"},
            )
        except Exception as exc:
            if "exists" not in str(exc).lower():
                print(f"⚠  upload failed for {name}: {exc}")
                upload_ok = False
        if not upload_ok:
            continue
        public_url = f"{base}/storage/v1/object/public/{bucket}/{path}"
        media = {
            "deployment_id": deployment_id,
            "file_path": public_url,
            "file_name": name,
            "file_mediatype": "image/jpeg",
            "uploaded_by": SEED_ADMIN_ID,
            "timestamp": taken,
        }
        svc.table("media").insert({k: v for k, v in media.items() if v is not None}).execute()
        uploaded += 1

    print(f"\n✅ Demo media seeded: {uploaded} added, {skipped} already present, {dated} dated (deployment {deployment_id}).")

    written, kept, removed = seed_labels(svc, deployment_id, read_all(media_rows), labels)
    print(f"✅ Demo labels: {written} written, {kept} already in place, {removed} removed.")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed the demo project with sample images and their labels")
    p.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL", "http://localhost:54321"))
    p.add_argument("--service-role-key", default=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    # `or` (not getenv default) so an empty env var — common in CI when an
    # optional GitHub var is unset — falls back to the built-in default.
    p.add_argument("--project-id", default=os.getenv("DEMO_PROJECT_ID") or DEMO_PROJECT_ID)
    p.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p.add_argument("--labels", default=str(DEFAULT_LABELS), help="CSV of path, observation_type, scientific_name, vernacular_name")
    p.add_argument("--bucket", default=os.getenv("SUPABASE_MEDIA_BUCKET") or DEFAULT_BUCKET)
    p.add_argument("--limit", type=int, default=0, help="Cap number of images (0 = all)")
    args = p.parse_args()

    if not args.service_role_key:
        print("✗  SUPABASE_SERVICE_ROLE_KEY is required")
        sys.exit(1)
    labels_path = Path(args.labels)
    if not labels_path.is_file():
        print(f"✗  No label file at {labels_path}")
        sys.exit(1)
    labels = read_labels(labels_path)

    images = sorted(
        f for f in Path(args.fixtures).rglob("*")
        if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
    )
    if args.limit:
        images = images[: args.limit]
    if not images:
        print(f"✗  No images found under {args.fixtures}")
        sys.exit(1)

    base = args.supabase_url.rstrip("/")
    svc = create_client(base + "/", args.service_role_key)
    print(f"🔌 Connected to {args.supabase_url}")
    print(f"📁 {len(images)} fixture image(s), {len(labels)} label(s)")

    seed(svc, base, args.project_id, images, args.bucket, labels)


if __name__ == "__main__":
    main()
