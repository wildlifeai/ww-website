# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GBF contribution report (#371): each figure on fixed data, the reads, and the route's access check."""

from datetime import date, datetime, timezone
from itertools import count
from types import SimpleNamespace

import pytest

from app.domain import gbf_report
from app.domain.gbf_report import Period, build_report, camera_nights
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


def report(media, earlier=(), deployments=DEPLOYMENTS):
    return build_report(
        projects=[{"id": P1, "name": "Alpha"}],
        deployments=deployments,
        media=media,
        earlier_media=list(earlier),
        taxa=TAXA,
        first=FIRST,
        last=LAST,
        now=NOW,
    )


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
    r = build_report(
        projects=[{"id": P1, "name": "Alpha"}],
        deployments=deployments,
        media=[photo(D2, "2025-12-28T10:00:00+00:00", ai("Rattus rattus", "t-rat"))],
        earlier_media=[],
        taxa=TAXA,
        first=date(2025, 12, 27),
        last=date(2026, 1, 10),
        now=NOW,
    )
    assert r["camera_nights_by_month"] == [{"month": "2025-12", "camera_nights": 5}, {"month": "2026-01", "camera_nights": 10}]
    assert species(r["target_6"], "Rattus rattus")["monthly"] == [
        {"month": "2025-12", "detections": 1, "rate": 20.0},
        {"month": "2026-01", "detections": 0, "rate": 0.0},
    ]


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
        "user_roles": [{"user_id": "u1", "role": "project_viewer", "scope_type": "project", "scope_id": P1, "is_active": True, "deleted_at": None}],
    }


async def test_the_report_reads_live_rows_in_the_period(monkeypatch):
    db = FakeDB(_tables())
    monkeypatch.setattr(gbf_report, "create_service_client", db.client)
    r = await gbf_report.gbf_report([P1], FIRST, LAST)
    assert r is not None
    rat = species(r["target_6"], "Rattus rattus")
    assert rat["detections"] == 2  # m1 and m2; m3 is deleted, m4 is before and m5 after the period
    assert rat["first_detections"] == [{"deployment_id": D2, "deployment_name": "Stream", "date": "2026-01-08"}]
    assert r["target_4"]["species"] == []
    assert r["target_20"]["photos"] == 2
    assert r["target_20"]["sites"] == 2  # the deleted deployment is not one
    assert r["projects"] == [{"id": P1, "name": "Alpha"}]


async def test_a_deleted_or_unknown_project_is_no_report(monkeypatch):
    tables = _tables()
    tables["projects"][1]["deleted_at"] = "2026-01-05"
    monkeypatch.setattr(gbf_report, "create_service_client", FakeDB(tables).client)
    assert await gbf_report.gbf_report([P1, P2], FIRST, LAST) is None


# ── The route ───────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app import authz
    from app.config import settings
    from app.dependencies import get_current_user
    from app.main import app
    from app.middleware.rate_limit import limiter

    db = FakeDB(_tables())
    monkeypatch.setattr(authz, "create_service_client", db.client)
    monkeypatch.setattr(gbf_report, "create_service_client", db.client)
    monkeypatch.setattr(settings, "FF_GBF_REPORT_ENABLED", True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", app_metadata={"is_demo": True})
    limiter.reset()
    yield SimpleNamespace(client=TestClient(app), settings=settings)
    limiter.reset()
    app.dependency_overrides.clear()


def _get(api, projects, first="2026-01-01", last="2026-01-31"):
    return api.client.get("/api/reports/gbf", params={"project_ids": projects, "from": first, "to": last}, headers={"Authorization": "Bearer x"})


def test_route_answers_a_viewer_and_the_demo(api):
    res = _get(api, P1)
    assert res.status_code == 200
    assert res.json()["data"]["target_20"]["photos"] == 2


def test_route_refuses_a_project_the_caller_cannot_see(api):
    assert _get(api, f"{P1},{P2}").status_code == 404


def test_route_checks_its_input(api):
    assert _get(api, "not-a-uuid").status_code == 422
    assert _get(api, " , ").status_code == 422
    assert _get(api, P1, first="2026-02-01", last="2026-01-01").status_code == 422


def test_route_is_off_by_default(api, monkeypatch):
    monkeypatch.setattr(api.settings, "FF_GBF_REPORT_ENABLED", False)
    res = _get(api, P1)
    assert res.status_code == 200
    assert res.json()["error"]["code"] == "FEATURE_DISABLED"
