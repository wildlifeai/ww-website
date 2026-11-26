# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GBF report's counts against the database's own function (#456): ww-backend's
``gbf_report_counts`` gives what ``gbf_reference.py``, the Python it was ported from, gives on the
same rows, and the route builds its report from them with the caller's session.

``test_gbf_report.py`` pins each figure on fixed data; this proves the function counts what the
Python counted. Runs only when pointed at a LOCAL stack built from ww-backend ``dev``. It creates
an organisation, two projects, three cameras with a deployment each, four taxa, two signed-in
users (a viewer of both projects and a reviewer) and generated photos and observations over three
years, and deletes them all afterwards. From a ww-backend checkout:

    eval "$(npx supabase status -o env | sed -n -E 's/^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=/export WW_TEST_\\1=/p')"
    cd <ww-website>/backend && pytest tests/test_gbf_report_local_stack.py

Without those variables the test is skipped, so ``pytest`` stays offline.

The rows leave out the two cases where the function and the old route part on purpose
(ww-backend pull request #382): a label whose ``taxon_id`` names an unflagged taxon while its
name is a flagged one, and two flagged taxa whose names match after ``lower(trim())``.
"""

import asyncio
import os
import random
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests import gbf_reference

URL = os.environ.get("WW_TEST_API_URL")
ANON_KEY = os.environ.get("WW_TEST_ANON_KEY")
SERVICE_KEY = os.environ.get("WW_TEST_SERVICE_ROLE_KEY")

pytestmark = pytest.mark.skipif(not (URL and ANON_KEY and SERVICE_KEY), reason="needs WW_TEST_API_URL, WW_TEST_ANON_KEY and WW_TEST_SERVICE_ROLE_KEY")

if URL and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", URL):
    raise RuntimeError(f"WW_TEST_API_URL must be a local stack, got {URL}")

THREATENED = ["Vulnerable", "Endangered", "Critically Endangered"]
# Periods: a month, a year, all three years, and a day.
PERIODS = [
    (date(2025, 6, 1), date(2025, 6, 30)),
    (date(2025, 1, 1), date(2025, 12, 31)),
    (date(2024, 1, 1), date(2026, 12, 31)),
    (date(2025, 3, 3), date(2025, 3, 3)),
]


def _photos(rng: random.Random, deployments: list[str], taxa: SimpleNamespace, reviewers: list[str]) -> list[dict]:
    """About 240 photos over 2024 to 2026, a third of them in bursts 5 to 45 minutes apart (30 and
    31 minutes included), each with up to three observations of every kind the verdict weighs."""
    named = [(taxa.invasive, True), (taxa.threatened, True), (taxa.both, True), (taxa.plain, True), (f"Felis nemo {taxa.run}", False)]
    photos: list[dict] = []
    for _ in range(240):
        timed = [p for p in photos if p["timestamp"]]
        if timed and rng.random() < 0.35:
            prev = rng.choice(timed)
            dep = prev["deployment_id"]
            taken = datetime.fromisoformat(prev["timestamp"]) + timedelta(minutes=rng.choice([5, 20, 30, 31, 45]))
        else:
            dep = rng.choice(deployments)
            taken = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=rng.randrange(3 * 365 * 24 * 60))
        obs = []
        for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
            name, has_taxon = rng.choice(named + [(None, False)])
            o = {
                "observation_type": "blank" if name is None or rng.random() < 0.1 else "animal",
                "scientific_name": name if name is None or rng.random() < 0.8 else f"  {name.upper()} ",
                # Found by id or by name; an id always names the label's own taxon.
                "taxon_id": taxa.ids[name] if has_taxon and rng.random() < 0.6 else None,
                "deleted": rng.random() < 0.05,
            }
            kind = rng.choice(["ai", "ai", "human", "consensus", "legacy"])
            if kind == "ai":
                o.update(source_type="ai", review_status="ai_reviewed", classification_method="machine")
            elif kind == "human":
                who = rng.choice(reviewers)
                status = rng.choice(["human_reviewed", "expert_reviewed"])
                o.update(source_type="human", review_status=status, classification_method="human", reviewer_id=who, annotator_id=who)
            elif kind == "consensus":
                o.update(source_type="consensus", review_status=rng.choice(["ai_reviewed", "consensus_approved"]), classification_method="machine")
            else:
                o.update(source_type=None, review_status="unreviewed", classification_method=rng.choice(["human", "machine", None]))
            obs.append(o)
        photos.append(
            {
                "deployment_id": dep,
                "timestamp": None if rng.random() < 0.02 else taken.isoformat(),
                "deleted": rng.random() < 0.04,
                "observations": obs,
            }
        )
    return photos


@pytest.fixture
def stack(monkeypatch):
    from fastapi.testclient import TestClient
    from supabase import create_client

    from app.config import settings
    from app.main import app
    from app.middleware.rate_limit import limiter
    from app.services import supabase_client

    url = URL.rstrip("/") + "/"
    svc = create_client(url, SERVICE_KEY)
    monkeypatch.setattr(settings, "SUPABASE_URL", url)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", ANON_KEY)
    monkeypatch.setattr(settings, "FF_GBF_REPORT_ENABLED", True)
    monkeypatch.setattr(supabase_client, "_service_client", svc)
    limiter.reset()

    run = uuid.uuid4().hex[:10]
    password = uuid.uuid4().hex  # throwaway local users; nothing is stored
    users: dict[str, str] = {}
    org_id = None
    projects: list[str] = []
    devices: list[str] = []
    taxon_ids: list[str] = []
    try:
        org_id = svc.table("organisations").insert({"name": f"GBF test {run}", "slug": f"gbf-test-{run}"}).execute().data[0]["id"]
        for n in (1, 2):
            projects.append(svc.table("projects").insert({"name": f"GBF {run} {n}", "organisation_id": org_id}).execute().data[0]["id"])
        tokens: dict[str, str] = {}
        for who in ("viewer", "reviewer"):
            email = f"gbf-{who}-{run}@example.test"
            created = svc.auth.admin.create_user(
                {"email": email, "password": password, "email_confirm": True, "user_metadata": {"name": f"{who} {run}"}}
            )
            users[who] = created.user.id
            for project in projects:
                role = "project_viewer" if who == "viewer" else "project_member"
                svc.table("user_roles").insert({"user_id": created.user.id, "role": role, "scope_type": "project", "scope_id": project}).execute()
            tokens[who] = create_client(url, ANON_KEY).auth.sign_in_with_password({"email": email, "password": password}).session.access_token

        flags = {
            "invasive": (True, "Least Concern"),
            "threatened": (False, "Endangered"),
            "both": (True, "Vulnerable"),
            "plain": (False, "Least Concern"),
        }
        taxa = SimpleNamespace(run=run, ids={})
        for key, (invasive, status) in flags.items():
            name = f"Testus {key} {run}"
            row = {"scientific_name": name, "rank": "species", "invasive_status": invasive, "conservation_status": status}
            taxon_id = svc.table("taxa").insert(row).execute().data[0]["id"]
            taxon_ids.append(taxon_id)
            taxa.ids[name] = taxon_id
            setattr(taxa, key, name)

        # Closed deployments, so camera-nights do not depend on when the test runs.
        deployments = []
        for n, (project, start, end) in enumerate(
            [(projects[0], "2024-01-01", "2026-12-31"), (projects[0], "2024-06-01", "2025-09-30"), (projects[1], "2025-01-15", "2026-06-30")]
        ):
            device = svc.table("devices").insert({"name": f"GBF cam {run} {n}", "bluetooth_id": f"bt-gbf-{run}-{n}"}).execute().data[0]["id"]
            devices.append(device)
            row = {
                "name": f"Site {n}",
                "location_name": "Here",
                "project_id": project,
                "device_id": device,
                "deployment_start": f"{start}T00:00:00Z",
                "deployment_end": f"{end}T00:00:00Z",
            }
            deployments.append(svc.table("deployments").insert(row).execute().data[0]["id"])

        written = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i, p in enumerate(_photos(random.Random(456), deployments, taxa, list(users.values()))):
            media = {
                "deployment_id": p["deployment_id"],
                "file_path": f"gdrive://gbf-{run}-{i}",
                "timestamp": p["timestamp"],
                "deleted_at": "2026-01-01T00:00:00Z" if p["deleted"] else None,
            }
            media_id = svc.table("media").insert(media).execute().data[0]["id"]
            rows = []
            for o in p["observations"]:
                written += timedelta(seconds=1)  # the order written, which the verdict follows
                deleted = o.pop("deleted")
                rows.append(
                    {
                        **o,
                        "deployment_id": p["deployment_id"],
                        "media_id": media_id,
                        "created_at": written.isoformat(),
                        "deleted_at": "2026-01-01T00:00:00Z" if deleted else None,
                    }
                )
            if rows:
                svc.table("observations").insert(rows).execute()

        yield SimpleNamespace(client=TestClient(app), svc=svc, projects=projects, deployments=deployments, tokens=tokens)
    finally:
        if projects:
            dep_ids = [r["id"] for r in svc.table("deployments").select("id").in_("project_id", projects).execute().data]
            if dep_ids:
                svc.table("observations").delete().in_("deployment_id", dep_ids).execute()
                svc.table("media").delete().in_("deployment_id", dep_ids).execute()
                svc.table("deployments").delete().in_("id", dep_ids).execute()
            svc.table("projects").delete().in_("id", projects).execute()
        if devices:
            svc.table("devices").delete().in_("id", devices).execute()
        if taxon_ids:
            svc.table("taxa").delete().in_("id", taxon_ids).execute()
        if org_id:
            svc.table("organisations").delete().eq("id", org_id).execute()
        for user_id in users.values():
            svc.table("user_roles").delete().eq("user_id", user_id).execute()
            svc.auth.admin.delete_user(user_id)
        limiter.reset()


def _caller(stack, who="viewer"):
    """The client the route gets for that user's session."""
    from app.dependencies import get_user_client

    return asyncio.run(get_user_client(f"Bearer {stack.tokens[who]}"))


def _python_counts(stack, projects: list[str], first: date, last: date) -> dict:
    """The reference's counts from the same rows, read with the service role: every live photo of
    the projects' live deployments before the period's end, and every flagged taxon."""
    from app.domain.gbf_report import Period
    from app.services.db_utils import read_all

    svc = stack.svc
    period = Period.of_days(first, last)
    deployments = read_all(
        lambda: svc.table("deployments").select("id, project_id").in_("project_id", projects).is_("deleted_at", "null").order("id")
    )
    ids = [d["id"] for d in deployments]
    invasive = read_all(lambda: svc.table("taxa").select("id, scientific_name").eq("invasive_status", True).order("id"))
    threatened = read_all(lambda: svc.table("taxa").select("id, scientific_name").in_("conservation_status", THREATENED).order("id"))
    return gbf_reference.counts(
        deployments=deployments,
        media=gbf_reference.read_media(svc, ids, period.end, period.start),
        earlier_media=gbf_reference.read_media(svc, ids, period.start),
        taxa=list({t["id"]: t for t in invasive + threatened}.values()),
        first=first,
        last=last,
    )


def test_the_function_counts_what_the_python_counted(stack):
    from app.domain.gbf_report import Period, read_counts

    caller = _caller(stack)
    seen_flagged = 0
    for projects in (stack.projects, stack.projects[:1], stack.projects[1:]):
        for first, last in PERIODS:
            expected = _python_counts(stack, projects, first, last)
            assert read_counts(caller, projects, Period.of_days(first, last)) == expected, (projects, first, last)
            seen_flagged += len(expected["flagged"])
    assert seen_flagged > 0  # the data has flagged detections to compare

    # The service role has no auth.uid(): it counts nothing, which is why the route sends the caller's session.
    service = read_counts(stack.svc, stack.projects, Period.of_days(*PERIODS[2]))
    assert (service["photos"], service["deployments_with_photos"], service["flagged"]) == (0, [], [])


def test_the_route_builds_the_report_from_the_callers_counts(stack):
    from app.domain.gbf_report import build_report
    from app.domain.gbif_dataset import with_gbif_figures
    from app.services.db_utils import rows_of

    first, last = PERIODS[2]
    ids = ",".join(stack.projects)
    res = stack.client.get(
        "/api/reports/gbf",
        params={"project_ids": ids, "from": first.isoformat(), "to": last.isoformat()},
        headers={"Authorization": f"Bearer {stack.tokens['viewer']}"},
    )
    assert res.status_code == 200, res.text

    svc = stack.svc
    projects = rows_of(svc.table("projects").select("id, name, gbif_dataset_key").in_("id", stack.projects).order("name").execute())
    deployments = rows_of(
        svc.table("deployments")
        .select("id, name, project_id, device_id, deployment_start, deployment_end")
        .in_("project_id", stack.projects)
        .order("id")
        .execute()
    )
    counts = _python_counts(stack, stack.projects, first, last)
    named = {f["taxon_id"] for f in counts["flagged"]}
    taxa = rows_of(
        svc.table("taxa").select("id, scientific_name, common_name, conservation_status, invasive_status").in_("id", sorted(named)).execute()
    )
    expected = build_report(
        projects=projects, deployments=deployments, counts=counts, taxa=taxa, first=first, last=last, now=datetime.now(timezone.utc)
    )
    expected["target_21"]["datasets"] = asyncio.run(with_gbif_figures(expected["target_21"]["datasets"]))  # no keys: GBIF is not asked
    assert res.json()["data"] == expected
