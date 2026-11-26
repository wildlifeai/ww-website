# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GBF contribution report (#371): each figure on fixed data, counted by the Python reference
of ``gbf_report_counts`` (``gbf_reference.py``), the report built from the function's counts, the
reads, and the route's access check."""

from datetime import date, datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest

from app.domain import gbf_report
from app.domain.gbf_report import Period, build_report, camera_nights
from tests import gbf_reference
from tests.fake_postgrest import FakeDB

ORG = "0000000a-0000-0000-0000-000000000000"
P1 = "11111111-1111-1111-1111-111111111111"
P2 = "22222222-2222-2222-2222-222222222222"
D1, D2, D3 = "d0000001-0000-0000-0000-000000000000", "d0000002-0000-0000-0000-000000000000", "d0000003-0000-0000-0000-000000000000"
RAT = {"id": "t-rat", "scientific_name": "Rattus rattus", "common_name": "Ship rat", "conservation_status": "Least Concern", "invasive_status": True}
KIWI = {
    "id": "t-kiwi",
    "scientific_name": "Apteryx mantelli",
    "common_name": "North Island brown kiwi",
    "conservation_status": "Vulnerable",
    "invasive_status": False,
}
# Introduced in New Zealand, Endangered in its native range: counted under both targets.
RABBIT = {
    "id": "t-rabbit",
    "scientific_name": "Oryctolagus cuniculus",
    "common_name": "European rabbit",
    "conservation_status": "Endangered",
    "invasive_status": True,
}
TAXA = [RAT, KIWI, RABBIT]

FIRST, LAST = date(2026, 1, 1), date(2026, 1, 31)
NOW = datetime(2026, 1, 16, tzinfo=timezone.utc)

DEPLOYMENTS = [
    # 20 Dec to 10 Jan: 9 nights in January.
    {
        "id": D1,
        "name": "Ridge",
        "project_id": P1,
        "device_id": "cam-1",
        "deployment_start": "2025-12-20T00:00:00+00:00",
        "deployment_end": "2026-01-10T00:00:00+00:00",
    },
    # Open since 6 Jan, now is 16 Jan: 10 nights.
    {"id": D2, "name": "Stream", "project_id": P1, "device_id": "cam-2", "deployment_start": "2026-01-06T00:00:00+00:00", "deployment_end": None},
    # Ended before the period: not monitored.
    {
        "id": D3,
        "name": "Old",
        "project_id": P1,
        "device_id": "cam-3",
        "deployment_start": "2025-06-01T00:00:00+00:00",
        "deployment_end": "2025-07-01T00:00:00+00:00",
    },
]

_ids = count(1)


def ai(name, taxon_id=None, **extra):
    return {
        "id": f"o{next(_ids)}",
        "observation_type": "animal",
        "scientific_name": name,
        "taxon_id": taxon_id,
        "source_type": "ai",
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        **extra,
    }


def human(name, reviewer, taxon_id=None, observation_type="animal"):
    return {
        "id": f"o{next(_ids)}",
        "observation_type": observation_type,
        "scientific_name": name,
        "taxon_id": taxon_id,
        "source_type": "human",
        "review_status": "human_reviewed",
        "classification_method": "human",
        "reviewer_id": reviewer,
        "annotator_id": reviewer,
    }


def photo(deployment, ts, *obs):
    return {"id": f"m{next(_ids)}", "deployment_id": deployment, "timestamp": ts, "observations": list(obs)}


def report(media, earlier=(), deployments=DEPLOYMENTS, first=FIRST, last=LAST):
    """The report of these photos: counted by the reference, built from its counts."""
    counts = gbf_reference.counts(deployments=deployments, media=media, earlier_media=list(earlier), taxa=TAXA, first=first, last=last)
    return build_report(projects=[{"id": P1, "name": "Alpha"}], deployments=deployments, counts=counts, taxa=TAXA, first=first, last=last, now=NOW)


def species(target, name):
    return next(s for s in target["species"] if s["scientific_name"] == name)


# ── Camera-nights ───────────────────────────────────────────────────


def test_camera_nights_are_clipped_to_the_period():
    period = Period.of_days(FIRST, LAST)
    assert camera_nights(DEPLOYMENTS[0], period.start, period.end, NOW) == 9
    assert camera_nights(DEPLOYMENTS[1], period.start, period.end, NOW) == 10  # open: up to now
    assert camera_nights(DEPLOYMENTS[2], period.start, period.end, NOW) == 0
    assert camera_nights({"deployment_start": None}, period.start, period.end, NOW) == 0


def test_monitoring_effort():
    r = report(
        [
            photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
            photo(D1, "2026-01-02T22:00:00+00:00", ai(None, observation_type="blank"), human(None, "u1", observation_type="blank")),
            photo(D2, "2026-01-07T03:00:00+00:00", ai("Apteryx mantelli", "t-kiwi"), human("Apteryx mantelli", "u2", "t-kiwi")),
            photo(D2, "2026-01-08T03:00:00+00:00"),
        ]
    )
    assert r["target_20"] == {"cameras": 2, "sites": 2, "camera_nights": 19, "photos": 4, "photos_with_ai": 3, "photos_reviewed": 2}
    assert r["target_22"] == {"reviewers": 2}
    assert r["camera_nights_by_month"] == [{"month": "2026-01", "camera_nights": 19}]
    assert r["method_version"] == "gbf-1"
    assert r["period"] == {"from": "2026-01-01", "to": "2026-01-31"}
    # One detection from reviewed photos: the kiwi. The reviewed blank is no detection.
    assert r["target_21"] == {
        "reviewed_detections": 1,
        "datasets": [{"project_id": P1, "project_name": "Alpha", "dataset_key": None, "reviewed_detections": 1}],
    }


# ── Detections and the 30-minute rule ───────────────────────────────


def test_photos_within_30_minutes_of_each_other_are_one_detection():
    rats = [
        photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
        photo(D1, "2026-01-02T10:20:00+00:00", ai("Rattus rattus", "t-rat")),
        photo(D1, "2026-01-02T10:45:00+00:00", ai("Rattus rattus", "t-rat")),  # 25 min after the last: same detection
        photo(D1, "2026-01-02T11:16:00+00:00", ai("Rattus rattus", "t-rat")),  # 31 min after: a new one
        photo(D2, "2026-01-07T10:05:00+00:00", ai("Rattus rattus", "t-rat")),  # another deployment: its own
    ]
    rat = species(report(rats)["target_6"], "Rattus rattus")
    assert rat["detections"] == 3
    assert rat["sites_with"] == 2
    assert rat["rate"] == round(3 / 19 * 100, 2)
    assert rat["monthly"] == [{"month": "2026-01", "detections": 3, "rate": round(3 / 19 * 100, 2)}]


def test_empty_and_unnamed_photos_are_not_detections():
    r = report(
        [
            photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat"), human(None, "u1", observation_type="blank")),
            photo(D1, "2026-01-03T10:00:00+00:00", ai(None)),
        ]
    )
    assert r["target_6"]["species"] == []
    assert r["target_6"]["species_recorded"] == 0


def test_a_persons_verdict_wins_and_names_fall_back_to_the_scientific_name():
    r = report(
        [
            # The AI said rat, a person said kiwi: a kiwi detection.
            photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat"), human("Apteryx mantelli", "u1", "t-kiwi")),
            # No taxon_id: found by its name.
            photo(D2, "2026-01-07T10:00:00+00:00", ai("rattus rattus")),
        ]
    )
    assert species(r["target_4"], "Apteryx mantelli")["detections"] == 1
    assert species(r["target_6"], "Rattus rattus")["detections"] == 1


def test_a_threatened_invasive_species_counts_under_both_targets():
    r = report([photo(D1, "2026-01-02T10:00:00+00:00", ai("Oryctolagus cuniculus", "t-rabbit"))])
    assert species(r["target_6"], "Oryctolagus cuniculus")["detections"] == 1
    assert species(r["target_4"], "Oryctolagus cuniculus")["detections"] == 1
    assert r["target_4"]["species_recorded"] == 1
    assert r["target_6"]["sites_monitored"] == 2


def test_first_detections_are_deployments_that_never_had_the_species_before():
    media = [
        photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
        photo(D2, "2026-01-08T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
        photo(D2, "2026-01-09T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
    ]
    earlier = [
        photo(D1, "2025-12-24T10:00:00+00:00", ai("Rattus rattus", "t-rat")),  # seen at the ridge before
        photo(D2, "2025-12-24T10:00:00+00:00", ai("Apteryx mantelli", "t-kiwi")),  # another species
    ]
    rat = species(report(media, earlier)["target_6"], "Rattus rattus")
    assert rat["first_detections"] == [{"deployment_id": D2, "deployment_name": "Stream", "date": "2026-01-08"}]


def test_species_are_ordered_by_rate():
    r = report(
        [
            photo(D1, "2026-01-02T10:00:00+00:00", ai("Oryctolagus cuniculus", "t-rabbit")),
            photo(D1, "2026-01-02T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
            photo(D1, "2026-01-03T10:00:00+00:00", ai("Rattus rattus", "t-rat")),
        ]
    )
    assert [s["scientific_name"] for s in r["target_6"]["species"]] == ["Rattus rattus", "Oryctolagus cuniculus"]


def test_months_split_the_rate():
    deployments = [{**DEPLOYMENTS[1], "deployment_start": "2025-12-27T00:00:00+00:00"}]
    media = [photo(D2, "2025-12-28T10:00:00+00:00", ai("Rattus rattus", "t-rat"))]
    r = report(media, deployments=deployments, first=date(2025, 12, 27), last=date(2026, 1, 10))
    assert r["camera_nights_by_month"] == [{"month": "2025-12", "camera_nights": 5}, {"month": "2026-01", "camera_nights": 10}]
    assert species(r["target_6"], "Rattus rattus")["monthly"] == [
        {"month": "2025-12", "detections": 1, "rate": 20.0},
        {"month": "2026-01", "detections": 0, "rate": 0.0},
    ]


# ── The report from the function's counts ───────────────────────────

NO_COUNTS = {
    "photos": 0,
    "photos_with_ai": 0,
    "photos_reviewed": 0,
    "reviewers": 0,
    "deployments_with_photos": [],
    "flagged": [],
    "flagged_first": [],
    "reviewed_detections": [],
}


def from_counts(counts, deployments=DEPLOYMENTS, projects=({"id": P1, "name": "Alpha"},), taxa=TAXA, first=FIRST, last=LAST, now=NOW):
    return build_report(projects=list(projects), deployments=deployments, counts={**NO_COUNTS, **counts}, taxa=taxa, first=first, last=last, now=now)


def test_no_photos_leaves_the_effort():
    r = from_counts({})
    empty = {"sites_monitored": 2, "species_recorded": 0, "species": []}
    assert (r["target_6"], r["target_4"]) == (empty, empty)
    assert r["target_20"] == {"cameras": 2, "sites": 2, "camera_nights": 19, "photos": 0, "photos_with_ai": 0, "photos_reviewed": 0}
    assert r["target_21"] == {
        "reviewed_detections": 0,
        "datasets": [{"project_id": P1, "project_name": "Alpha", "dataset_key": None, "reviewed_detections": 0}],
    }
    assert r["target_22"] == {"reviewers": 0}

    bare = from_counts({}, deployments=[])  # a project with no deployment
    assert bare["camera_nights_by_month"] == [{"month": "2026-01", "camera_nights": 0}]
    assert bare["target_20"]["sites"] == 0 and bare["target_6"]["sites_monitored"] == 0


def test_the_counts_become_the_species_figures():
    r = from_counts(
        {
            "photos": 6,
            "photos_with_ai": 5,
            "photos_reviewed": 2,
            "reviewers": 1,
            # The old deployment had photos and no camera-nights: still a site.
            "deployments_with_photos": [D1, D3],
            "flagged": [
                {"taxon_id": "t-kiwi", "deployment_id": D1, "month": "2026-01", "detections": 1},
                {"taxon_id": "t-rat", "deployment_id": D1, "month": "2026-01", "detections": 2},
                {"taxon_id": "t-rat", "deployment_id": D2, "month": "2026-01", "detections": 1},
            ],
            "flagged_first": [
                {"taxon_id": "t-kiwi", "deployment_id": D1, "first_detection_at": "2026-01-05T00:00:00.000000Z", "seen_before": True},
                {"taxon_id": "t-rat", "deployment_id": D2, "first_detection_at": "2026-01-03T01:00:00.000000Z", "seen_before": False},
                {"taxon_id": "t-rat", "deployment_id": D1, "first_detection_at": "2026-01-03T23:59:59.999999Z", "seen_before": False},
            ],
            "reviewed_detections": [{"project_id": P1, "detections": 2}],
        },
        taxa=[RAT, KIWI],
    )
    rat_rate = round(3 / 19 * 100, 2)
    assert species(r["target_6"], "Rattus rattus") == {
        "taxon_id": "t-rat",
        "scientific_name": "Rattus rattus",
        "common_name": "Ship rat",
        "conservation_status": "Least Concern",
        "invasive": True,
        "detections": 3,
        "rate": rat_rate,
        "sites_with": 2,
        "monthly": [{"month": "2026-01", "detections": 3, "rate": rat_rate}],
        # The same UTC day: by the deployment's name.
        "first_detections": [
            {"deployment_id": D1, "deployment_name": "Ridge", "date": "2026-01-03"},
            {"deployment_id": D2, "deployment_name": "Stream", "date": "2026-01-03"},
        ],
    }
    kiwi = species(r["target_4"], "Apteryx mantelli")
    assert (kiwi["detections"], kiwi["sites_with"], kiwi["first_detections"]) == (1, 1, [])  # seen there before the period
    assert r["target_6"]["species_recorded"] == 1 and r["target_4"]["species_recorded"] == 1
    assert r["target_20"] == {"cameras": 3, "sites": 3, "camera_nights": 19, "photos": 6, "photos_with_ai": 5, "photos_reviewed": 2}
    assert r["target_21"]["reviewed_detections"] == 2
    assert r["target_22"] == {"reviewers": 1}


def test_several_years_list_every_month():
    deployments = [{**DEPLOYMENTS[1], "deployment_start": "2024-01-01T00:00:00+00:00", "deployment_end": "2026-12-31T00:00:00+00:00"}]
    r = from_counts(
        {
            "photos": 3,
            "deployments_with_photos": [D2],
            "flagged": [
                {"taxon_id": "t-rat", "deployment_id": D2, "month": "2024-03", "detections": 1},
                {"taxon_id": "t-rat", "deployment_id": D2, "month": "2026-11", "detections": 2},
            ],
            "flagged_first": [{"taxon_id": "t-rat", "deployment_id": D2, "first_detection_at": "2024-03-04T05:06:07.000000Z", "seen_before": False}],
        },
        deployments=deployments,
        first=date(2024, 1, 1),
        last=date(2026, 12, 31),
        now=datetime(2027, 1, 5, tzinfo=timezone.utc),
    )
    every_month = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]
    assert [m["month"] for m in r["camera_nights_by_month"]] == every_month
    assert r["camera_nights_by_month"][-1] == {"month": "2026-12", "camera_nights": 30}  # ended on the 31st
    assert r["target_20"]["camera_nights"] == 366 + 365 + 364
    rat = species(r["target_6"], "Rattus rattus")
    assert rat["detections"] == 3
    assert [m["month"] for m in rat["monthly"]] == every_month
    assert {m["month"]: m["detections"] for m in rat["monthly"] if m["detections"]} == {"2024-03": 1, "2026-11": 2}
    assert rat["first_detections"] == [{"deployment_id": D2, "deployment_name": "Stream", "date": "2024-03-04"}]


def test_reviewed_detections_are_per_project():
    beta_site = {**DEPLOYMENTS[1], "id": D3, "name": "Beta site", "project_id": P2, "device_id": "cam-9"}
    r = from_counts(
        {"reviewed_detections": [{"project_id": P2, "detections": 4}]},
        deployments=[DEPLOYMENTS[0], beta_site],
        projects=[{"id": P1, "name": "Alpha"}, {"id": P2, "name": "Beta", "gbif_dataset_key": "k-beta"}],
    )
    assert r["projects"] == [{"id": P1, "name": "Alpha"}, {"id": P2, "name": "Beta"}]
    assert r["target_21"] == {
        "reviewed_detections": 4,
        "datasets": [
            {"project_id": P1, "project_name": "Alpha", "dataset_key": None, "reviewed_detections": 0},
            {"project_id": P2, "project_name": "Beta", "dataset_key": "k-beta", "reviewed_detections": 4},
        ],
    }
    assert r["target_20"]["sites"] == 2 and r["target_20"]["cameras"] == 2


# ── Reads, against the fake PostgREST ───────────────────────────────


def _tables():
    obs = []

    def stored(media_id, deployment, o, deleted=None):
        obs.append(
            {**o, "media_id": media_id, "deployment_id": deployment, "deleted_at": deleted, "created_at": f"2026-01-01T00:00:{len(obs):02d}+00:00"}
        )

    media = [
        {"id": "m1", "deployment_id": D1, "timestamp": "2026-01-02T10:00:00+00:00", "deleted_at": None, "created_at": "2026-01-02"},
        {"id": "m2", "deployment_id": D2, "timestamp": "2026-01-08T10:00:00+00:00", "deleted_at": None, "created_at": "2026-01-08"},
        {"id": "m3", "deployment_id": D2, "timestamp": "2026-01-09T10:00:00+00:00", "deleted_at": "2026-01-10", "created_at": "2026-01-09"},
        {"id": "m4", "deployment_id": D1, "timestamp": "2025-12-24T10:00:00+00:00", "deleted_at": None, "created_at": "2025-12-24"},
        {"id": "m5", "deployment_id": D1, "timestamp": "2026-02-02T10:00:00+00:00", "deleted_at": None, "created_at": "2026-02-02"},
    ]
    stored("m1", D1, ai("Rattus rattus", "t-rat"))
    stored("m2", D2, ai("Rattus rattus", "t-rat"))
    stored("m2", D2, human("Apteryx mantelli", "u2", "t-kiwi"), deleted="2026-01-09")  # a deleted verdict: the AI's stands
    stored("m3", D2, ai("Rattus rattus", "t-rat"))
    stored("m4", D1, ai("Rattus rattus", "t-rat"))
    stored("m5", D1, ai("Rattus rattus", "t-rat"))
    return {
        "projects": [
            {"id": P1, "name": "Alpha", "organisation_id": ORG, "deleted_at": None},
            {"id": P2, "name": "Beta", "organisation_id": ORG, "deleted_at": None},
        ],
        "deployments": [{**d, "deleted_at": None} for d in DEPLOYMENTS]
        + [
            {
                "id": "d0000004-0000-0000-0000-000000000000",
                "name": "Gone",
                "project_id": P1,
                "device_id": "cam-4",
                "deployment_start": "2026-01-01",
                "deleted_at": "2026-01-02",
            }
        ],
        "media": media,
        "observations": obs,
        "taxa": [{**t, "created_at": None} for t in TAXA],
        "user_roles": [
            {"user_id": "u1", "role": "project_viewer", "scope_type": "project", "scope_id": P1, "is_active": True, "deleted_at": None},
            {"user_id": "u2", "role": "organisation_member", "scope_type": "organisation", "scope_id": ORG, "is_active": True, "deleted_at": None},
            {"user_id": "u3", "role": "organisation_manager", "scope_type": "organisation", "scope_id": ORG, "is_active": True, "deleted_at": None},
        ],
    }


def _counts_function(db):
    """A stand-in for ``gbf_report_counts`` over the fake's tables: the reference's counts of the
    projects' live deployments, without the caller's access, which the route checks here."""

    def run(params):
        start = datetime.fromisoformat(params["p_from"])
        end = datetime.fromisoformat(params["p_to"])
        deployments = [d for d in db.rows("deployments") if d["project_id"] in params["p_project_ids"] and d.get("deleted_at") is None]
        ids = [d["id"] for d in deployments]
        flagged = [t for t in db.rows("taxa") if t.get("invasive_status") or t.get("conservation_status") in gbf_report.THREATENED_STATUSES]
        return gbf_reference.counts(
            deployments=deployments,
            media=gbf_reference.read_media(db, ids, end, start),
            earlier_media=gbf_reference.read_media(db, ids, start),
            taxa=flagged,
            first=start.date(),
            last=(end - timedelta(days=1)).date(),
        )

    return run


def _clients(tables):
    """The service client, which has no function, and the caller's, which has only ``gbf_report_counts``."""
    return FakeDB(tables), FakeDB({}, rpcs={gbf_report.COUNTS_FUNCTION: _counts_function(FakeDB(tables))})


async def test_the_report_counts_live_rows_in_the_period_as_the_caller(monkeypatch):
    svc, caller = _clients(_tables())
    monkeypatch.setattr(gbf_report, "create_service_client", svc.client)
    r = await gbf_report.gbf_report(caller, [P1], FIRST, LAST)
    assert r is not None
    assert caller.calls == [
        ("gbf_report_counts", {"p_project_ids": [P1], "p_from": "2026-01-01T00:00:00+00:00", "p_to": "2026-02-01T00:00:00+00:00"})
    ]
    rat = species(r["target_6"], "Rattus rattus")
    assert rat["detections"] == 2  # m1 and m2; m3 is deleted, m4 is before and m5 after the period
    assert rat["first_detections"] == [{"deployment_id": D2, "deployment_name": "Stream", "date": "2026-01-08"}]
    assert r["target_4"]["species"] == []
    assert r["target_20"]["photos"] == 2
    assert r["target_20"]["sites"] == 2  # the deleted deployment is not one
    assert r["projects"] == [{"id": P1, "name": "Alpha"}]
    # Only the taxa the counts name are read.
    assert [rq.filters for rq in svc.requests if rq.table == "taxa"] == [[("id", "in", ["t-rat"])]]
    assert caller.requests == []  # the caller's session only calls the function


async def test_a_deleted_or_unknown_project_is_no_report(monkeypatch):
    tables = _tables()
    tables["projects"][1]["deleted_at"] = "2026-01-05"
    svc, caller = _clients(tables)
    monkeypatch.setattr(gbf_report, "create_service_client", svc.client)
    assert await gbf_report.gbf_report(caller, [P1, P2], FIRST, LAST) is None
    assert caller.calls == []


def test_counts_that_are_not_an_object_fail_loudly():
    caller = FakeDB({}, rpcs={gbf_report.COUNTS_FUNCTION: lambda params: []})
    with pytest.raises(RuntimeError, match="not an object"):
        gbf_report.read_counts(caller, [P1], Period.of_days(FIRST, LAST))


# ── The route ───────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app import authz
    from app.config import settings
    from app.dependencies import get_current_user, get_user_client
    from app.main import app
    from app.middleware.rate_limit import limiter

    svc, caller = _clients(_tables())
    monkeypatch.setattr(authz, "create_service_client", svc.client)
    monkeypatch.setattr(gbf_report, "create_service_client", svc.client)
    monkeypatch.setattr(settings, "FF_GBF_REPORT_ENABLED", True)

    def as_user(user_id, demo=False):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, app_metadata={"is_demo": demo})

    as_user("u1", demo=True)
    app.dependency_overrides[get_user_client] = lambda: caller
    limiter.reset()
    yield SimpleNamespace(client=TestClient(app), settings=settings, caller=caller, as_user=as_user)
    limiter.reset()
    app.dependency_overrides.clear()


def _get(api, projects, first="2026-01-01", last="2026-01-31"):
    return api.client.get("/api/reports/gbf", params={"project_ids": projects, "from": first, "to": last}, headers={"Authorization": "Bearer x"})


def test_route_answers_a_viewer_and_the_demo(api):
    res = _get(api, P1)
    assert res.status_code == 200
    assert res.json()["data"]["target_20"]["photos"] == 2
    assert [name for name, _ in api.caller.calls] == ["gbf_report_counts"]


def test_route_refuses_a_project_the_caller_cannot_see(api):
    assert _get(api, f"{P1},{P2}").status_code == 404
    assert api.caller.calls == []  # refused before anything is counted


def test_route_refuses_an_organisation_member_without_a_project_role(api):
    """The database would count no photo for them, so the route answers as for an unknown project."""
    api.as_user("u2")
    assert _get(api, P1).status_code == 404
    assert api.caller.calls == []
    api.as_user("u3")  # an organisation manager reads every project of the organisation
    assert _get(api, f"{P1},{P2}").status_code == 200


def _role(role, scope_type, scope_id, **extra):
    return {"role": role, "scope_type": scope_type, "scope_id": scope_id, **extra}


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (_role("project_viewer", "project", P1), True),
        (_role("project_member", "project", P1), True),
        (_role("project_admin", "project", P1), True),
        (_role("ww_admin", "system", None), True),
        (_role("organisation_manager", "organisation", ORG), True),
        (_role("organisation_member", "organisation", ORG), False),
        (_role("project_viewer", "project", P2), False),  # another project
        (_role("organisation_manager", "organisation", "another-org"), False),
        (_role("project_viewer", "project", P1, expires_at="2000-01-01T00:00:00+00:00"), False),
    ],
)
def test_can_view_follows_has_project_role_viewer(role, allowed):
    from app.authz import _can_view

    assert _can_view([role], ORG, P1) is allowed


def test_route_checks_its_input(api):
    assert _get(api, "not-a-uuid").status_code == 422
    assert _get(api, " , ").status_code == 422
    assert _get(api, P1, first="2026-02-01", last="2026-01-01").status_code == 422


def test_route_is_off_by_default(api, monkeypatch):
    monkeypatch.setattr(api.settings, "FF_GBF_REPORT_ENABLED", False)
    res = _get(api, P1)
    assert res.status_code == 200
    assert res.json()["error"]["code"] == "FEATURE_DISABLED"
