#!/usr/bin/env python3
"""Dev vs prod parity audit.

Three silent parity gaps reached production in two days (26 Jul 2026):
missing media RLS predicate, missing GOOGLE_DRIVE_* config, missing
FF_MEDIA_REGISTRY_ENABLED + media-renditions bucket. Each was invisible
until a user hit it. This script diffs the surfaces those gaps lived on:

  1. Container-app env var NAMES (values compared for flags only)
  2. Container-app secret NAMES (never values)
  3. Supabase Storage buckets (name + public flag)
  4. Supabase Auth external providers (public settings endpoint)
  5. SQL surface (policies / grants / functions / realtime publication) —
     printed as a query to run in both SQL editors; pass the saved JSON
     outputs back in with --sql-dev/--sql-prod to have them diffed.

Requirements: Python 3.9+, `az` CLI logged in with access to the WW-AE
resource group. No third-party packages; secrets are read via `az` and
used only in Authorization headers, never printed.

Usage:
  python scripts/parity_audit.py                 # sections 1-4 + SQL to run
  python scripts/parity_audit.py --sql-dev dev.json --sql-prod prod.json

Exit code 1 when unexplained gaps exist (CI-friendly).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

RG = "WW-AE"
DEV_APP = "ww-backend-dev"
PROD_APP = "ww-backend"
SERVICE_KEY_SECRET = "supabase-service-key"

# Intentional differences, with the reason they are fine. Anything NOT in
# this list is reported as an unexplained gap. Keep reasons honest — an
# entry here is a documented decision, not a way to silence the report.
EXPECTED_ENV_DIFFS: dict[str, str] = {}

# Vars that must exist on BOTH sides with DIFFERENT values. These never
# belong in EXPECTED_ENV_DIFFS (that list excuses absence, and losing one
# of these is a real gap) - for them the dangerous state is identical
# values, i.e. one environment pointing at the other's infrastructure.
MUST_DIFFER: dict[str, str] = {
    "SUPABASE_URL": "identical URL = one env talking to the other's database",
    "ALLOWED_ORIGINS": "each API must allow only its own frontend origins",
    "GOOGLE_DRIVE_FOLDER_ID": "each env archives into its own Drive root",
    "AZURE_STORAGE_CONTAINER_NAME": "each env buffers uploads in its own container",
    "REDIS_URL": "identical broker = prod jobs run on the dev worker against the dev database",
}
EXPECTED_SECRET_DIFFS: dict[str, str] = {
    "pg-conn": "KEDA scaler pooler creds live on each worker app, not the API",
    "hf-token": "gated DINOv3 weights only needed where the worker runs",
}
EXPECTED_BUCKET_DIFFS: dict[str, str] = {
    "analysis-images": "legacy prod-only bucket predating the renditions design",
}

# Env vars whose VALUES must match across environments (feature behaviour),
# not just exist. Everything else only has its presence compared.
VALUE_COMPARED_PREFIXES = ("FF_",)
VALUE_COMPARED_SUFFIXES = ("_ENABLED",)


def az(args: list[str]):
    """Run az and parse JSON output.

    Resolved via shutil.which so the .cmd shim works on Windows without
    shell=True - args stay a real argv list and are never re-parsed by a
    shell, so nothing is injectable even once app/RG become CLI flags.
    """
    exe = shutil.which("az")
    if not exe:
        sys.exit("az CLI not found on PATH - install it and run: az login")
    res = subprocess.run([exe, *args, "-o", "json"],
                         capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        sys.exit(f"az failed: az {' '.join(args)}\n{res.stderr.strip()[:400]}")
    return json.loads(res.stdout or "null")


def http_json(url: str, key: str | None = None, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    if key:
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def app_env(app: str) -> dict[str, dict]:
    env = az(["containerapp", "show", "-n", app, "-g", RG, "--query",
              "properties.template.containers[0].env"]) or []
    return {e["name"]: e for e in env}


def app_secret_names(app: str) -> set[str]:
    rows = az(["containerapp", "secret", "list", "-n", app, "-g", RG,
               "--query", "[].name"]) or []
    return set(rows)


def supabase_of(app: str, envs: dict[str, dict]) -> tuple[str, str]:
    url = (envs.get("SUPABASE_URL") or {}).get("value") or ""
    key = az(["containerapp", "secret", "show", "-n", app, "-g", RG,
              "--secret-name", SERVICE_KEY_SECRET, "--query", "value"])
    if not url or not key:
        sys.exit(f"could not resolve Supabase URL/key for {app}")
    return url.rstrip("/"), key


def buckets(url: str, key: str) -> dict[str, bool]:
    return {b["name"]: bool(b.get("public")) for b in http_json(f"{url}/storage/v1/bucket", key)}


def auth_providers(url: str, key: str) -> dict[str, bool]:
    s = http_json(f"{url}/auth/v1/settings", key)
    return {k: bool(v) for k, v in (s.get("external") or {}).items() if v}


def value_compared(name: str) -> bool:
    return name.startswith(VALUE_COMPARED_PREFIXES) or name.endswith(VALUE_COMPARED_SUFFIXES)


SQL_QUERY = """-- Run in BOTH SQL editors, save each result as JSON, then:
--   python scripts/parity_audit.py --sql-dev dev.json --sql-prod prod.json
select 'policy' as kind, tablename as name,
       policyname || ' [' || cmd || '] ' || coalesce(qual, '') as detail
  from pg_policies where schemaname = 'public'
union all
select 'grant', table_name, grantee || ':' || string_agg(privilege_type, ',' order by privilege_type)
  from information_schema.role_table_grants
 where table_schema = 'public' and grantee in ('authenticated', 'anon')
 group by table_name, grantee
union all
select 'function', p.proname,
       -- hash the STORED body with CR stripped, plus the clauses prosrc omits.
       -- pg_get_functiondef embeds prosrc verbatim, so CRLF-applied prod vs
       -- LF-applied dev reported 18 phantom drifts (26 Jul) for byte-identical
       -- functions; translate(...,chr(13),'') makes the hash ending-neutral.
       md5(translate(p.prosrc, chr(13), '')
           || '|' || p.prosecdef::text
           || '|' || coalesce(array_to_string(p.proconfig, ','), '')
           || '|' || pg_get_function_identity_arguments(p.oid))
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname = 'public'
   and p.prokind = 'f'  -- aggregates (PostGIS) have no plain body
   and not exists (      -- extension-owned functions are vendor code, not app schema
     select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
union all
select 'realtime', schemaname || '.' || tablename, 'in supabase_realtime publication'
  from pg_publication_tables where pubname = 'supabase_realtime'
order by 1, 2, 3;"""


def load_sql_rows(path: str) -> set[tuple[str, str, str]]:
    rows = json.load(open(path, encoding="utf-8-sig"))
    return {(r["kind"], r["name"], (r.get("detail") or "").strip()) for r in rows}


def report(title: str, gaps: list[str], expected: list[str]) -> int:
    print(f"\n== {title} ==")
    for line in expected:
        print(f"   (expected) {line}")
    if not gaps:
        print("   OK — no unexplained differences")
        return 0
    for line in gaps:
        print(f"   GAP {line}")
    return len(gaps)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql-dev")
    ap.add_argument("--sql-prod")
    args = ap.parse_args()
    problems = 0

    dev_env, prod_env = app_env(DEV_APP), app_env(PROD_APP)

    # ── 1. Env vars ──
    gaps, expected = [], []
    for name in sorted(set(dev_env) | set(prod_env)):
        in_dev, in_prod = name in dev_env, name in prod_env
        if in_dev != in_prod:
            side = "prod" if in_dev else "dev"
            line = f"{name}: missing in {side}"
            (expected.append(f"{line} — {EXPECTED_ENV_DIFFS[name]}")
             if name in EXPECTED_ENV_DIFFS else gaps.append(line))
        elif value_compared(name):
            dv = (dev_env[name].get("value") or "").lower()
            pv = (prod_env[name].get("value") or "").lower()
            if dv != pv:
                gaps.append(f"{name}: dev={dv or '<secretref>'} prod={pv or '<secretref>'}")
        elif name in MUST_DIFFER:
            dv = (dev_env[name].get("value") or "")
            pv = (prod_env[name].get("value") or "")
            if dv and pv and dv == pv:
                gaps.append(f"{name}: identical in both envs - {MUST_DIFFER[name]}")
    problems += report(f"Env vars ({DEV_APP} vs {PROD_APP})", gaps, expected)

    # ── 2. Secrets (names only) ──
    dev_sec, prod_sec = app_secret_names(DEV_APP), app_secret_names(PROD_APP)
    gaps, expected = [], []
    for name in sorted(dev_sec ^ prod_sec):
        side = "prod" if name in dev_sec else "dev"
        line = f"{name}: missing in {side}"
        (expected.append(f"{line} — {EXPECTED_SECRET_DIFFS[name]}")
         if name in EXPECTED_SECRET_DIFFS else gaps.append(line))
    problems += report("Secrets (names only)", gaps, expected)

    dev_url, dev_key = supabase_of(DEV_APP, dev_env)
    prod_url, prod_key = supabase_of(PROD_APP, prod_env)

    # ── 3. Storage buckets ──
    db, pb = buckets(dev_url, dev_key), buckets(prod_url, prod_key)
    gaps, expected = [], []
    for name in sorted(set(db) | set(pb)):
        if (name in db) != (name in pb):
            side = "prod" if name in db else "dev"
            line = f"{name}: missing in {side}"
            (expected.append(f"{line} — {EXPECTED_BUCKET_DIFFS[name]}")
             if name in EXPECTED_BUCKET_DIFFS else gaps.append(line))
        elif db[name] != pb[name]:
            gaps.append(f"{name}: public flag differs (dev={db[name]} prod={pb[name]})")
    problems += report("Storage buckets", gaps, expected)

    # ── 4. Auth providers ──
    try:
        dp, pp = auth_providers(dev_url, dev_key), auth_providers(prod_url, prod_key)
        gaps = [f"{name}: enabled only in {'dev' if name in dp else 'prod'}"
                for name in sorted(set(dp) ^ set(pp))]
        problems += report("Auth external providers", gaps, [])
    except Exception as exc:  # settings endpoint shape varies across gotrue versions
        print(f"\n== Auth external providers ==\n   skipped ({exc})")

    # ── 5. SQL surface ──
    if args.sql_dev and args.sql_prod:
        dev_rows, prod_rows = load_sql_rows(args.sql_dev), load_sql_rows(args.sql_prod)
        gaps = []
        for kind, name, detail in sorted(dev_rows - prod_rows):
            gaps.append(f"{kind} {name}: only in dev — {detail[:110]}")
        for kind, name, detail in sorted(prod_rows - dev_rows):
            gaps.append(f"{kind} {name}: only in prod — {detail[:110]}")
        problems += report("SQL surface (policies/grants/functions/realtime)", gaps, [])
    else:
        print("\n== SQL surface ==\n   run this in BOTH SQL editors, then re-run with --sql-dev/--sql-prod:\n")
        print(SQL_QUERY)

    print(f"\n{'PARITY GAPS FOUND: ' + str(problems) if problems else 'No unexplained gaps.'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
