# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Export projects, deployments, and devices from Supabase as CSV files.

Usage:
    python scripts/export_data.py

Requires SUPABASE_URL and SUPABASE_ANON_KEY in .env (see .env.example).
Authenticates with your email/password and exports only data visible to you (RLS-scoped).

Output files are written to exports/ with timestamped names:
    exports/projects_2026-03-22.csv
    exports/deployments_2026-03-22.csv
    exports/devices_2026-03-22.csv
"""

import csv
import os
import sys
import getpass
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Resolve paths relative to repo root (parent of scripts/)
REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORTS_DIR = REPO_ROOT / "exports"


def init_supabase():
    """Create an authenticated Supabase client."""
    load_dotenv(REPO_ROOT / ".env")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")

    if not url or not key:
        print("❌ Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")
        print("   Copy .env.example to .env and fill in your Supabase project settings.")
        sys.exit(1)

    if not url.endswith("/"):
        url += "/"

    client = create_client(url, key)

    # Interactive login
    print("🔐 Supabase Login")
    email = input("   Email: ").strip()
    password = getpass.getpass("   Password: ")

    try:
        client.auth.sign_in_with_password({"email": email, "password": password})
        print(f"   ✅ Authenticated as {email}\n")
    except Exception as e:
        print(f"   ❌ Login failed: {e}")
        sys.exit(1)

    return client


def fetch_all_rows(client, table: str, select: str, order_by: str = "created_at"):
    """Fetch all rows from a table, handling Supabase's 1000-row default limit."""
    all_rows = []
    offset = 0
    page_size = 1000

    while True:
        response = (
            client.table(table)
            .select(select)
            .is_("deleted_at", "null")
            .order(order_by, desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        if not response.data:
            break

        all_rows.extend(response.data)

        if len(response.data) < page_size:
            break  # Last page

        offset += page_size

    return all_rows


def write_csv(rows: list, filepath: Path, columns: list):
    """Write rows to a CSV file with the given column order."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_projects(client, today: str):
    """Export projects to CSV."""
    columns = [
        "id", "name", "description", "organisation_id",
        "capture_method_id", "activity_detection_sensitivity_id",
        "timelapse_interval_seconds", "model_id",
        "is_active", "is_baited",
        "created_at", "updated_at",
    ]

    rows = fetch_all_rows(client, "projects", ", ".join(columns))
    filepath = EXPORTS_DIR / f"projects_{today}.csv"
    write_csv(rows, filepath, columns)
    print(f"   ✅ {len(rows)} projects  → {filepath.name}")
    return len(rows)


def export_deployments(client, today: str):
    """Export deployments to CSV."""
    columns = [
        "id", "name", "project_id", "device_id", "device_preparation_id",
        "deployment_start", "deployment_end", "deployment_status_id",
        "setup_by", "ended_by",
        "location_name", "location_description",
        "latitude", "longitude", "altitude", "accuracy",
        "camera_height",
        "capture_method_id", "activity_detection_sensitivity_id",
        "timelapse_interval_seconds",
        "start_deployment_comments", "end_deployment_comments",
        "created_at", "updated_at",
    ]

    rows = fetch_all_rows(client, "deployments", ", ".join(columns), order_by="deployment_start")
    filepath = EXPORTS_DIR / f"deployments_{today}.csv"
    write_csv(rows, filepath, columns)
    print(f"   ✅ {len(rows)} deployments → {filepath.name}")
    return len(rows)


def export_devices(client, today: str):
    """Export devices to CSV."""
    columns = [
        "id", "name", "bluetooth_id", "device_eui",
        "organisation_id",
        "created_at", "updated_at",
    ]

    rows = fetch_all_rows(client, "devices", ", ".join(columns))
    filepath = EXPORTS_DIR / f"devices_{today}.csv"
    write_csv(rows, filepath, columns)
    print(f"   ✅ {len(rows)} devices    → {filepath.name}")
    return len(rows)


def main():
    client = init_supabase()
    today = date.today().isoformat()

    print(f"📦 Exporting data to {EXPORTS_DIR}/")

    total = 0
    total += export_projects(client, today)
    total += export_deployments(client, today)
    total += export_devices(client, today)

    if total == 0:
        print("\n⚠️  No data found. Your account may not have access to any projects yet.")
    else:
        print(f"\n✨ Done — {total} total records exported.")
        print(f"   Deployment IDs in these CSVs match the EXIF deployment_id in camera images.")


if __name__ == "__main__":
    main()
