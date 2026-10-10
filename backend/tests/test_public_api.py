# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Public Data API (/api/v1) with API keys (#307, #327): each endpoint's organisation
scoping, scopes, paging, the per-key rate limit, the export and its jobs.

The database is ``tests/fake_postgrest.py``, which evaluates the endpoints' PostgREST queries
over two organisations' rows and fails on a column ww-backend's schema does not have.
"""

import hashlib
import uuid
from types import SimpleNamespace

import pytest

from app.domain.public_api import photo_verdict
from app.services.api_key import KEY_PREFIX, VALID_SCOPES, generate_api_key
from tests.fake_postgrest import FakeDB


class TestApiKeyGeneration:
    def test_key_has_prefix(self):
        raw_key, key_hash = generate_api_key()
        assert raw_key.startswith(KEY_PREFIX)

    def test_key_length(self):
        raw_key, _ = generate_api_key()
        # ww_live_ + 16 hex chars = "ww_live_" (8) + 16 = 24
        assert len(raw_key) > len(KEY_PREFIX)

    def test_hash_is_sha256(self):
        raw_key, key_hash = generate_api_key()
        expected = hashlib.sha256(raw_key.encode()).hexdigest()
        assert key_hash == expected

    def test_keys_are_unique(self):
        key1, _ = generate_api_key()
        key2, _ = generate_api_key()
        assert key1 != key2

    def test_valid_scopes_exist(self):
        assert "deployments:read" in VALID_SCOPES
        assert "telemetry:read" in VALID_SCOPES
        assert "export:camtrapdp" in VALID_SCOPES
        assert len(VALID_SCOPES) == 6


# ── Two organisations ────────────────────────────────────────────────────────


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


ORG_A, ORG_B = _id(1), _id(2)
PROJECT_A, PROJECT_A_DELETED, PROJECT_B = _id(11), _id(12), _id(13)
DEP_A, DEP_A_DELETED, DEP_IN_DELETED_PROJECT, DEP_B = _id(21), _id(22), _id(23), _id(24)
CAM_A, CAM_B = _id(31), _id(32)
# Org A's photos: a rat, an empty photo (consensus blank over a SpeciesNet rat), a deleted photo,
# a photo with no observation, and one whose only observation is deleted.
PHOTO_RAT, PHOTO_EMPTY, PHOTO_DELETED, PHOTO_BARE, PHOTO_OBS_DELETED = _id(41), _id(42), _id(43), _id(44), _id(45)
PHOTO_IN_DELETED_DEP, PHOTO_IN_DELETED_PROJECT, PHOTO_B = _id(46), _id(47), _id(48)

ALL_SCOPES = sorted(VALID_SCOPES)
RAW = {name: KEY_PREFIX + hashlib.md5(name.encode()).hexdigest() for name in ("a", "a_devices", "a_revoked", "b")}


def _key(name, n, org, scopes, revoked_at=None):
    return {
        "id": _id(n),
        "organisation_id": org,
        "name": name,
        "key_hash": hashlib.sha256(RAW[name].encode()).hexdigest(),
        "key_prefix": RAW[name][:16],
        "scopes": scopes,
        "expires_at": None,
        "revoked_at": revoked_at,
    }


def _obs(n, media, deployment, created, **fields):
    row = {
        "id": _id(n),
        "media_id": media,
        "deployment_id": deployment,
        "observation_type": "animal",
        "scientific_name": None,
        "review_status": "ai_reviewed",
        "source_type": "ai",
        "classification_method": "machine",
        "deleted_at": None,
        "created_at": created,
    }
    row.update(fields)
    return row


def _media(media_id, deployment, created, deleted_at=None):
    return {
        "id": media_id,
        "deployment_id": deployment,
        "file_path": "gdrive://x",
        "timestamp": created,
        "deleted_at": deleted_at,
        "created_at": created,
    }


GONE = "2026-10-01T00:00:00+00:00"


def _tables():
    return {
        "projects": [
            {"id": PROJECT_A, "name": "Kiwi", "organisation_id": ORG_A, "deleted_at": None},
            {"id": PROJECT_A_DELETED, "name": "Old", "organisation_id": ORG_A, "deleted_at": GONE},
            {"id": PROJECT_B, "name": "Rats", "organisation_id": ORG_B, "deleted_at": None},
        ],
        "deployment_statuses": [{"id": 1, "value": "started"}, {"id": 2, "value": "ended"}],
        "devices": [
            {
                "id": CAM_A,
                "name": "Cam A",
                "bluetooth_id": "AA",
                "device_eui": "EUI-A",
                "organisation_id": ORG_A,
                "deleted_at": None,
                "created_at": "2026-01-01",
            },
            {
                "id": CAM_B,
                "name": "Cam B",
                "bluetooth_id": "BB",
                "device_eui": "EUI-B",
                "organisation_id": ORG_B,
                "deleted_at": None,
                "created_at": "2026-01-02",
            },
        ],
        "deployments": [
            {
                "id": DEP_A,
                "name": "A1",
                "project_id": PROJECT_A,
                "device_id": CAM_A,
                "deployment_status_id": 1,
                "deleted_at": None,
                "created_at": "2026-02-01",
            },
            {
                "id": DEP_A_DELETED,
                "name": "A2",
                "project_id": PROJECT_A,
                "device_id": CAM_A,
                "deployment_status_id": 2,
                "deleted_at": GONE,
                "created_at": "2026-02-02",
            },
            {
                "id": DEP_IN_DELETED_PROJECT,
                "name": "A3",
                "project_id": PROJECT_A_DELETED,
                "device_id": CAM_A,
                "deployment_status_id": 1,
                "deleted_at": None,
                "created_at": "2026-02-03",
            },
            # Org A's camera, lent to org B's project (ww-backend#320).
            {
                "id": DEP_B,
                "name": "B1",
                "project_id": PROJECT_B,
                "device_id": CAM_A,
                "deployment_status_id": 1,
                "deleted_at": None,
                "created_at": "2026-02-04",
            },
        ],
        "media": [
            _media(PHOTO_RAT, DEP_A, "2026-03-01T00:00:00+00:00"),
            _media(PHOTO_EMPTY, DEP_A, "2026-03-02T00:00:00+00:00"),
            _media(PHOTO_DELETED, DEP_A, "2026-03-03T00:00:00+00:00", deleted_at=GONE),
            _media(PHOTO_BARE, DEP_A, "2026-03-04T00:00:00+00:00"),
            _media(PHOTO_OBS_DELETED, DEP_A, "2026-03-05T00:00:00+00:00"),
            _media(PHOTO_IN_DELETED_DEP, DEP_A_DELETED, "2026-03-06T00:00:00+00:00"),
            _media(PHOTO_IN_DELETED_PROJECT, DEP_IN_DELETED_PROJECT, "2026-03-07T00:00:00+00:00"),
            _media(PHOTO_B, DEP_B, "2026-03-08T00:00:00+00:00"),
        ],
        "observations": [
            _obs(51, PHOTO_RAT, DEP_A, "2026-03-01T00:00:01", scientific_name="Rattus rattus"),
            _obs(52, PHOTO_EMPTY, DEP_A, "2026-03-02T00:00:01", scientific_name="Rattus rattus"),
            _obs(53, PHOTO_EMPTY, DEP_A, "2026-03-02T00:00:02", source_type="consensus", observation_type="blank"),
            _obs(54, PHOTO_DELETED, DEP_A, "2026-03-03T00:00:01", scientific_name="Felis catus"),
            _obs(55, PHOTO_OBS_DELETED, DEP_A, "2026-03-05T00:00:01", scientific_name="Felis catus", deleted_at=GONE),
            _obs(56, PHOTO_IN_DELETED_DEP, DEP_A_DELETED, "2026-03-06T00:00:01", scientific_name="Felis catus"),
            _obs(57, PHOTO_IN_DELETED_PROJECT, DEP_IN_DELETED_PROJECT, "2026-03-07T00:00:01", scientific_name="Felis catus"),
            _obs(58, PHOTO_B, DEP_B, "2026-03-08T00:00:01", scientific_name="Mus musculus", review_status="human_reviewed", source_type="human"),
        ],
        "lorawan_messages": [
            {"id": _id(61), "device_eui": "EUI-A", "deployment_id": DEP_A, "received_at": "2026-04-01T00:00:00+00:00"},
            {"id": _id(62), "device_eui": "EUI-A", "deployment_id": DEP_B, "received_at": "2026-04-02T00:00:00+00:00"},
            {"id": _id(63), "device_eui": "EUI-A", "deployment_id": None, "received_at": "2026-04-03T00:00:00+00:00"},
            {"id": _id(64), "device_eui": "EUI-A", "deployment_id": DEP_A_DELETED, "received_at": "2026-04-04T00:00:00+00:00"},
        ],
        "lorawan_parsed_messages": [
            {"id": _id(71), "lorawan_message_id": _id(61), "battery_level": 90, "sd_card_used_capacity": 10, "model_output": "rat:87"},
            {"id": _id(72), "lorawan_message_id": _id(62), "battery_level": 80, "sd_card_used_capacity": 20, "model_output": "none"},
        ],
        "api_keys": [
            _key("a", 81, ORG_A, ALL_SCOPES),
            _key("a_devices", 82, ORG_A, ["devices:read"]),
            _key("a_revoked", 83, ORG_A, ALL_SCOPES, revoked_at=GONE),
            _key("b", 84, ORG_B, ALL_SCOPES),
        ],
        "api_jobs": [],
    }


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.middleware.rate_limit import limiter

    db = FakeDB(_tables())
    for target in ("app.domain.public_api", "app.services.api_key", "app.jobs.store"):
        monkeypatch.setattr(f"{target}.create_service_client", db.client)
    monkeypatch.setattr(settings, "FF_PUBLIC_API_ENABLED", True)
    monkeypatch.setattr(settings, "FF_CAMTRAPDP_EXPORT_ENABLED", True)
    enqueued = []

    async def enqueue(name, *args, **kwargs):
        enqueued.append((name, args))
        return "local"

    monkeypatch.setattr("app.routers.public_api.enqueue_job", enqueue)
    limiter.reset()
    client = TestClient(app)

    def call(method, path, key="a", **kwargs):
        headers = {"X-API-Key": RAW[key]} if key else {}
        return client.request(method, path, headers=headers, **kwargs)

    yield SimpleNamespace(call=call, client=client, db=db, enqueued=enqueued, settings=settings)
    limiter.reset()
    app.dependency_overrides.clear()


def _ids(resp, field="id"):
    assert resp.status_code == 200, resp.text
    return [r[field] for r in resp.json()["data"]]


# ── Keys and scopes ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/api/v1/deployments", "deployments:read"),
        (f"/api/v1/deployments/{DEP_A}", "deployments:read"),
        ("/api/v1/devices", "devices:read"),
        ("/api/v1/devices/EUI-A/telemetry", "telemetry:read"),
        ("/api/v1/observations", "observations:read"),
        (f"/api/v1/jobs/{_id(99)}", "export:camtrapdp"),
    ],
)
def test_each_endpoint_needs_a_valid_key_with_its_scope(api, path, scope):
    assert api.call("GET", path, key=None).status_code == 401
    assert api.call("GET", path, key="a_revoked").status_code == 401
    assert api.client.get(path, headers={"X-API-Key": KEY_PREFIX + "0" * 32}).status_code == 401
    refused = api.call("GET", path, key="a_devices")
    if scope == "devices:read":
        assert refused.status_code == 200
    else:
        assert refused.status_code == 403
        assert scope in refused.json()["detail"]


def test_export_needs_its_scope(api):
    assert api.call("POST", "/api/v1/export/camtrapdp", key="a_devices", json={"project_id": PROJECT_A}).status_code == 403
    assert api.enqueued == []


def test_the_api_is_off_without_its_flag(api, monkeypatch):
    monkeypatch.setattr(api.settings, "FF_PUBLIC_API_ENABLED", False)
    assert api.call("GET", "/api/v1/devices").status_code == 404


# ── Organisation scoping ─────────────────────────────────────────────────────


def test_deployments_are_scoped_through_their_project(api):
    resp = api.call("GET", "/api/v1/deployments")
    assert _ids(resp) == [DEP_A]
    row = resp.json()["data"][0]
    assert (row["project_name"], row["device_name"], row["status"]) == ("Kiwi", "Cam A", "started")
    assert "projects" not in row and resp.json()["meta"]["total"] == 1

    assert _ids(api.call("GET", "/api/v1/deployments", key="b")) == [DEP_B]
    assert _ids(api.call("GET", "/api/v1/deployments", params={"project_id": PROJECT_B})) == []
    assert _ids(api.call("GET", "/api/v1/deployments", params={"status": "ended"})) == []
    assert _ids(api.call("GET", "/api/v1/deployments", params={"status": "started"})) == [DEP_A]


def test_a_deployment_outside_the_organisation_is_not_found(api):
    assert api.call("GET", f"/api/v1/deployments/{DEP_A}").json()["data"]["id"] == DEP_A
    for other in (DEP_B, DEP_A_DELETED, DEP_IN_DELETED_PROJECT):
        assert api.call("GET", f"/api/v1/deployments/{other}").status_code == 404
    assert api.call("GET", f"/api/v1/deployments/{DEP_A}", key="b").status_code == 404
    assert api.call("GET", "/api/v1/deployments/not-a-uuid").status_code == 422


def test_devices_are_the_organisations_own(api):
    assert _ids(api.call("GET", "/api/v1/devices")) == [CAM_A]
    assert _ids(api.call("GET", "/api/v1/devices", key="b")) == [CAM_B]


def test_telemetry_follows_the_deployment_not_the_camera(api):
    """Org A's camera, lent to org B: each organisation gets what it sent in its own deployments."""
    a = api.call("GET", "/api/v1/devices/EUI-A/telemetry").json()["data"]
    assert [(p["deployment_id"], p["battery_level"], p["model_output"]) for p in a] == [(DEP_A, 90, "rat:87")]
    b = api.call("GET", "/api/v1/devices/EUI-A/telemetry", key="b").json()["data"]
    assert [p["deployment_id"] for p in b] == [DEP_B]
    assert api.call("GET", "/api/v1/devices/EUI-B/telemetry").json()["data"] == []


def test_observations_are_one_verdict_per_live_photo_of_the_organisation(api):
    resp = api.call("GET", "/api/v1/observations")
    data = resp.json()["data"]
    assert [d["media_id"] for d in data] == [PHOTO_RAT, PHOTO_EMPTY]
    assert resp.json()["meta"]["total"] == 2
    rat, empty = data
    assert (rat["scientific_name"], rat["is_empty"], rat["observation_id"], rat["project_id"]) == ("Rattus rattus", False, _id(51), PROJECT_A)
    assert (empty["is_empty"], empty["observation_id"], empty["scientific_name"]) == (True, None, None)

    b = api.call("GET", "/api/v1/observations", key="b").json()["data"]
    assert [(d["media_id"], d["scientific_name"], d["human_reviewed"]) for d in b] == [(PHOTO_B, "Mus musculus", True)]


def test_observation_filters_stay_inside_the_organisation(api):
    assert api.call("GET", "/api/v1/observations", params={"deployment_id": DEP_B}).json()["data"] == []
    assert api.call("GET", "/api/v1/observations", params={"project_id": PROJECT_B}).json()["data"] == []
    only = api.call("GET", "/api/v1/observations", params={"project_id": PROJECT_A, "deployment_id": DEP_A}).json()["data"]
    assert [d["media_id"] for d in only] == [PHOTO_RAT, PHOTO_EMPTY]


# ── Paging ───────────────────────────────────────────────────────────────────


def test_observations_page_with_limit_and_offset_in_a_stable_order(api):
    first = api.call("GET", "/api/v1/observations", params={"limit": 1}).json()
    second = api.call("GET", "/api/v1/observations", params={"limit": 1, "offset": 1}).json()
    past = api.call("GET", "/api/v1/observations", params={"limit": 1, "offset": 2}).json()
    assert [d["media_id"] for d in first["data"] + second["data"]] == [PHOTO_RAT, PHOTO_EMPTY]
    assert (first["meta"]["total"], first["meta"]["page"], second["meta"]["page"]) == (2, 1, 2)
    assert past["data"] == []


@pytest.mark.parametrize("path", ["/api/v1/observations", "/api/v1/deployments", "/api/v1/devices", "/api/v1/devices/EUI-A/telemetry"])
def test_a_page_larger_than_postgrest_returns_is_refused(api, path):
    assert api.call("GET", path, params={"limit": 1000}).status_code == 200
    assert api.call("GET", path, params={"limit": 1001}).status_code == 422


# ── Export and jobs ──────────────────────────────────────────────────────────


def test_export_starts_the_originals_job_for_the_keys_organisation(api):
    resp = api.call(
        "POST", "/api/v1/export/camtrapdp", json={"project_id": PROJECT_A, "deployment_ids": [DEP_A], "date_from": "2026-01-01T00:00:00Z"}
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["job_id"]
    [(name, (queued_id, selection, caller))] = api.enqueued
    assert (name, queued_id) == ("export_camtrapdp_originals_job", job_id)
    assert selection == {"project_id": PROJECT_A, "deployment_ids": [DEP_A], "date_from": "2026-01-01T00:00:00+00:00", "date_to": None}
    assert caller == {"user_token": None, "organisation_id": ORG_A}


@pytest.mark.parametrize("project", [PROJECT_B, PROJECT_A_DELETED, _id(999)])
def test_export_of_a_project_outside_the_organisation_is_not_found(api, project):
    assert api.call("POST", "/api/v1/export/camtrapdp", json={"project_id": project}).status_code == 404
    assert api.enqueued == []


def test_export_is_off_without_its_flag(api, monkeypatch):
    monkeypatch.setattr(api.settings, "FF_CAMTRAPDP_EXPORT_ENABLED", False)
    assert api.call("POST", "/api/v1/export/camtrapdp", json={"project_id": PROJECT_A}).status_code == 404
    assert api.enqueued == []


def test_a_job_is_read_only_by_its_organisations_keys(api):
    from app.dependencies import get_current_user
    from app.main import app

    job_id = api.call("POST", "/api/v1/export/camtrapdp", json={"project_id": PROJECT_A}).json()["data"]["job_id"]

    mine = api.call("GET", f"/api/v1/jobs/{job_id}")
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"]["status"] == "queued" and mine.json()["data"]["result_url"] is None
    assert "events" not in mine.json()["data"]
    assert api.call("GET", f"/api/v1/jobs/{job_id}", key="b").status_code == 404
    assert api.call("GET", f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404

    # A signed-in user's polling does not reach it either: its link is the organisation's data.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="someone", app_metadata={})
    assert api.client.get(f"/api/jobs/{job_id}", headers={"Authorization": "Bearer x"}).status_code == 404


def test_a_users_job_is_not_readable_with_a_key(api):
    import asyncio

    from app.jobs.store import create_job

    job_id = asyncio.run(create_job(user_id="someone", kind="export"))
    assert api.call("GET", f"/api/v1/jobs/{job_id}").status_code == 404


# ── Rate limit ───────────────────────────────────────────────────────────────


def test_rate_limit_is_per_key_with_retry_after(api, monkeypatch):
    monkeypatch.setattr(api.settings, "PUBLIC_API_RATE_LIMIT_PER_MINUTE", 3)
    for _ in range(3):
        assert api.call("GET", "/api/v1/devices").status_code == 200
    limited = api.call("GET", "/api/v1/observations")
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    # Same address, another key: its own allowance.
    assert api.call("GET", "/api/v1/devices", key="b").status_code == 200


def test_exports_have_a_lower_limit_of_their_own(api):
    for _ in range(5):
        assert api.call("POST", "/api/v1/export/camtrapdp", json={"project_id": PROJECT_A}).status_code == 200
    limited = api.call("POST", "/api/v1/export/camtrapdp", json={"project_id": PROJECT_A})
    assert limited.status_code == 429 and "Retry-After" in limited.headers
    assert len(api.enqueued) == 5
    assert api.call("GET", "/api/v1/devices").status_code == 200


# ── The verdict matches the grid's (frontend lib/observations.test.ts) ───────

RAT = {"id": "speciesnet", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "animal", "scientific_name": "Rattus rattus"}
SN_BLANK = {"id": "speciesnet", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "blank", "scientific_name": None}
GEMINI = {"id": "gemini", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "animal", "scientific_name": None}


def _consensus(kind):
    return {"id": "consensus", "source_type": "consensus", "review_status": "ai_reviewed", "observation_type": kind, "scientific_name": None}


def test_verdict_keeps_the_first_row_without_a_consensus():
    assert photo_verdict([SN_BLANK, GEMINI]) == (SN_BLANK, True)
    assert photo_verdict([RAT, SN_BLANK]) == (RAT, False)
    assert photo_verdict([]) == (None, False)


def test_verdict_follows_the_consensus():
    assert photo_verdict([RAT, _consensus("blank")]) == (None, True)
    assert photo_verdict([SN_BLANK, GEMINI, _consensus("animal")]) == (None, False)
    assert photo_verdict([_consensus("animal"), SN_BLANK, RAT]) == (RAT, False)


def test_a_human_verdict_overrules_the_consensus():
    human_blank = {"id": "human", "source_type": "human", "review_status": "human_reviewed", "observation_type": "blank", "scientific_name": None}
    assert photo_verdict([RAT, _consensus("animal"), human_blank]) == (human_blank, True)
    corrected = {**RAT, "review_status": "human_reviewed", "scientific_name": "Rattus norvegicus"}
    assert photo_verdict([_consensus("blank"), corrected]) == (corrected, False)
    approved_blank = {**SN_BLANK, "review_status": "consensus_approved"}
    assert photo_verdict([GEMINI, _consensus("animal"), approved_blank]) == (approved_blank, True)
    confirmed = {**_consensus("animal"), "review_status": "human_reviewed"}
    reviewed_rat = {**RAT, "review_status": "human_reviewed"}
    assert photo_verdict([confirmed, reviewed_rat]) == (reviewed_rat, False)
