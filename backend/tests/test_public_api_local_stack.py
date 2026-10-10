# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Public Data API against real PostgREST (#327): two organisations, and each one's key
sees only its own deployments, cameras, telemetry, observations, export and jobs.

``test_public_api.py`` runs the same rules on a fake; this proves the embeds and nested
filters mean on PostgREST what the fake takes them to mean. Runs only when pointed at a LOCAL
stack built from ww-backend ``dev``, as ``test_api_keys_local_stack.py`` explains. It creates
two organisations with a project, a camera, a deployment, a photo with an observation, a
LoRaWAN message and an API key each, and deletes them all afterwards.
"""

import hashlib
import os
import re
import uuid
from types import SimpleNamespace

import pytest

URL = os.environ.get("WW_TEST_API_URL")
SERVICE_KEY = os.environ.get("WW_TEST_SERVICE_ROLE_KEY")

pytestmark = pytest.mark.skipif(not (URL and SERVICE_KEY), reason="needs WW_TEST_API_URL and WW_TEST_SERVICE_ROLE_KEY")

if URL and not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?/?$", URL):
    raise RuntimeError(f"WW_TEST_API_URL must be a local stack, got {URL}")


def _seed(svc, run: str, n: int) -> SimpleNamespace:
    """One organisation with one of everything the API reads, and a key with every scope."""
    org = svc.table("organisations").insert({"name": f"Public API test {run} {n}", "slug": f"public-api-test-{run}-{n}"}).execute().data[0]["id"]
    project = svc.table("projects").insert({"name": f"Project {n}", "organisation_id": org}).execute().data[0]["id"]
    eui = f"70B3D57ED00{run[:4].upper()}{n}"
    device = (
        svc.table("devices")
        .insert({"name": f"Cam {n}", "bluetooth_id": f"bt-{run}-{n}", "organisation_id": org, "device_eui": eui})
        .execute()
        .data[0]["id"]
    )
    deployment = (
        svc.table("deployments")
        .insert({"name": f"Dep {n}", "location_name": "Here", "deployment_start": "2026-10-01T00:00:00Z", "project_id": project, "device_id": device})
        .execute()
        .data[0]["id"]
    )
    media = svc.table("media").insert({"deployment_id": deployment, "file_path": f"gdrive://{run}-{n}"}).execute().data[0]["id"]
    observation = (
        svc.table("observations")
        .insert(
            {"deployment_id": deployment, "media_id": media, "observation_type": "animal", "scientific_name": f"Species {n}", "source_type": "ai"}
        )
        .execute()
        .data[0]["id"]
    )
    message = svc.table("lorawan_messages").insert({"device_eui": eui, "deployment_id": deployment, "raw_payload": {}}).execute().data[0]["id"]
    svc.table("lorawan_parsed_messages").insert({"lorawan_message_id": message, "battery_level": 50 + n}).execute()
    raw = "ww_live_" + uuid.uuid4().hex
    scopes = ["deployments:read", "devices:read", "telemetry:read", "observations:read", "export:camtrapdp"]
    svc.table("api_keys").insert(
        {"organisation_id": org, "name": "test", "key_hash": hashlib.sha256(raw.encode()).hexdigest(), "key_prefix": raw[:16], "scopes": scopes}
    ).execute()
    return SimpleNamespace(
        org=org, project=project, device=device, eui=eui, deployment=deployment, media=media, observation=observation, message=message, key=raw
    )


def _remove(svc, o: SimpleNamespace) -> None:
    svc.table("observations").delete().eq("id", o.observation).execute()
    svc.table("media").delete().eq("id", o.media).execute()
    svc.table("lorawan_messages").delete().eq("id", o.message).execute()  # cascades to the parsed row
    svc.table("deployments").delete().eq("id", o.deployment).execute()
    svc.table("devices").delete().eq("id", o.device).execute()
    svc.table("projects").delete().eq("id", o.project).execute()
    svc.table("organisations").delete().eq("id", o.org).execute()  # cascades to its api_keys


@pytest.fixture
def stack(monkeypatch):
    from fastapi.testclient import TestClient
    from supabase import create_client

    from app.config import settings
    from app.main import app
    from app.middleware.rate_limit import limiter
    from app.services import supabase_client

    svc = create_client(URL.rstrip("/") + "/", SERVICE_KEY)
    monkeypatch.setattr(supabase_client, "_service_client", svc)
    monkeypatch.setattr(settings, "FF_PUBLIC_API_ENABLED", True)
    monkeypatch.setattr(settings, "FF_CAMTRAPDP_EXPORT_ENABLED", True)
    enqueued = []

    async def enqueue(name, *args, **kwargs):
        enqueued.append(args)
        return "local"

    monkeypatch.setattr("app.routers.public_api.enqueue_job", enqueue)
    limiter.reset()

    run = uuid.uuid4().hex[:8]
    seeded = []
    try:
        for n in (1, 2):
            seeded.append(_seed(svc, run, n))
        yield SimpleNamespace(client=TestClient(app), svc=svc, a=seeded[0], b=seeded[1], enqueued=enqueued)
    finally:
        jobs = [args[0] for args in enqueued]
        if jobs:
            svc.table("api_jobs").delete().in_("id", jobs).execute()
        for o in seeded:
            _remove(svc, o)
        limiter.reset()


def test_each_key_sees_only_its_organisation(stack):
    c, a, b = stack.client, stack.a, stack.b

    def get(path, who, **params):
        return c.get(path, headers={"X-API-Key": who.key}, params=params)

    for me, other in ((a, b), (b, a)):
        deps = get("/api/v1/deployments", me)
        assert deps.status_code == 200, deps.text
        assert [d["id"] for d in deps.json()["data"]] == [me.deployment]
        assert get(f"/api/v1/deployments/{me.deployment}", me).status_code == 200
        assert get(f"/api/v1/deployments/{other.deployment}", me).status_code == 404

        assert [d["id"] for d in get("/api/v1/devices", me).json()["data"]] == [me.device]

        mine = get(f"/api/v1/devices/{me.eui}/telemetry", me)
        assert mine.status_code == 200, mine.text
        assert [p["deployment_id"] for p in mine.json()["data"]] == [me.deployment]
        assert get(f"/api/v1/devices/{other.eui}/telemetry", me).json()["data"] == []

        obs = get("/api/v1/observations", me)
        assert obs.status_code == 200, obs.text
        assert [(o["media_id"], o["observation_id"], o["project_id"]) for o in obs.json()["data"]] == [(me.media, me.observation, me.project)]
        assert get("/api/v1/observations", me, deployment_id=other.deployment).json()["data"] == []

        assert c.post("/api/v1/export/camtrapdp", headers={"X-API-Key": me.key}, json={"project_id": other.project}).status_code == 404

    started = c.post("/api/v1/export/camtrapdp", headers={"X-API-Key": a.key}, json={"project_id": a.project})
    assert started.status_code == 200, started.text
    job_id = started.json()["data"]["job_id"]
    assert stack.enqueued[0][2] == {"user_token": None, "organisation_id": a.org}
    assert get(f"/api/v1/jobs/{job_id}", a).status_code == 200
    assert get(f"/api/v1/jobs/{job_id}", b).status_code == 404


def test_deleted_rows_drop_out(stack):
    c, a, svc = stack.client, stack.a, stack.svc
    headers = {"X-API-Key": a.key}
    svc.table("media").update({"deleted_at": "now()"}).eq("id", a.media).execute()
    assert c.get("/api/v1/observations", headers=headers).json()["data"] == []
    svc.table("projects").update({"deleted_at": "now()"}).eq("id", a.project).execute()
    assert c.get("/api/v1/deployments", headers=headers).json()["data"] == []
    assert c.get(f"/api/v1/devices/{a.eui}/telemetry", headers=headers).json()["data"] == []
