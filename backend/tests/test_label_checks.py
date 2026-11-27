# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Label checks (#448): the signals on synthetic fingerprints, the recompute against the
``label_checks`` rows (outcomes kept as the record decision 15 counts), the nightly trigger and
the routes."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import authz
from app.config import settings
from app.dependencies import get_current_user
from app.domain import label_check_runs as runs
from app.domain.label_checks import LOOK_ALIKE, MORE_SPECIFIC, ODD_ONE_OUT, Labelled, Tuning, compute_flags, nearest
from app.jobs import label_checks as jobs
from app.routers import label_checks as router
from tests.fake_postgrest import FakeDB

DIM = 16
TUNE = Tuning(k=4, min_examples=4, odd_z=3.0)
_rng = {"gen": np.random.default_rng(7)}


@pytest.fixture(autouse=True)
def _same_noise_every_test():
    """Each test draws the same noise whatever runs before it."""
    _rng["gen"] = np.random.default_rng(7)


def _around(centre: np.ndarray, n: int, spread: float = 0.05) -> np.ndarray:
    return centre + spread * _rng["gen"].standard_normal((n, DIM))


def _axis(i: int) -> np.ndarray:
    v = np.zeros(DIM)
    v[i] = 1.0
    return v


def _items(names: list[str], confirmed: bool = True, taxa: dict | None = None) -> list[Labelled]:
    return [Labelled(f"o{i}", f"m{i}", n, (taxa or {}).get(n), confirmed, "r1") for i, n in enumerate(names)]


def _kinds(flags, observation_id):
    return {f.kind: f for f in flags if f.observation_id == observation_id}


# ── Signals ──────────────────────────────────────────────────────────


def test_nearest_never_returns_the_row_itself():
    x = np.eye(4, dtype=np.float32)
    x[1] = x[0]
    idx, dist = nearest(x, [0, 1], [0, 1, 2, 3], 2)
    assert idx[0][0] == 1 and idx[1][0] == 0
    assert dist[0][0] == pytest.approx(0.0)


def test_a_label_among_another_species_is_a_look_alike_and_an_odd_one_out():
    rats, mice = _around(_axis(0), 8), _around(_axis(1), 8)
    wrong = _around(_axis(1), 1)  # labelled rat, looks like the mice
    names = ["Rattus rattus"] * 8 + ["Mus musculus"] * 8 + ["Rattus rattus"]
    flags = compute_flags(_items(names), np.vstack([rats, mice, wrong]), {}, TUNE)

    mine = _kinds(flags, "o16")
    assert mine[LOOK_ALIKE].other_scientific_name == "Mus musculus" and mine[LOOK_ALIKE].score == 1.0
    assert mine[LOOK_ALIKE].label_scientific_name == "Rattus rattus" and mine[LOOK_ALIKE].reference == "confirmed"
    assert mine[ODD_ONE_OUT].score == 1.0
    # Normal variation is not flagged.
    assert {f.observation_id for f in flags} == {"o16"}


def test_a_broader_name_among_its_narrower_one_could_be_more_specific():
    taxa = {
        "t-rattus": {"id": "t-rattus", "scientific_name": "Rattus", "rank": "genus", "genus": "Rattus"},
        "t-rat": {"id": "t-rat", "scientific_name": "Rattus rattus", "rank": "species", "genus": "Rattus", "species": "rattus"},
    }
    names = ["Rattus rattus"] * 8 + ["Rattus"]
    items = _items(names, taxa={"Rattus": "t-rattus", "Rattus rattus": "t-rat"})
    flags = compute_flags(items, np.vstack([_around(_axis(0), 9)]), taxa, TUNE)
    assert [(f.observation_id, f.kind, f.other_scientific_name, f.other_taxon_id) for f in flags] == [("o8", MORE_SPECIFIC, "Rattus rattus", "t-rat")]


def test_a_species_with_too_few_examples_is_not_checked_for_odd_ones():
    names = ["Rattus rattus"] * 3
    vectors = np.vstack([_around(_axis(0), 2), _around(_axis(5), 1)])
    assert compute_flags(_items(names), vectors, {}, TUNE) == []


def test_ai_labels_make_the_reference_when_too_few_are_confirmed():
    rats, mice = _around(_axis(0), 6), _around(_axis(1), 6)
    names = ["Rattus rattus"] * 6 + ["Mus musculus"] * 6 + ["Rattus rattus"]
    items = [Labelled(f"o{i}", f"m{i}", n, None, n == "Rattus rattus", "r1") for i, n in enumerate(names)]
    flags = compute_flags(items, np.vstack([rats, mice, _around(_axis(1), 1)]), {}, TUNE)
    assert _kinds(flags, "o12")[LOOK_ALIKE].reference == "ai"
    assert _kinds(flags, "o12")[ODD_ONE_OUT].reference == "confirmed"


# ── Recompute against the rows ───────────────────────────────────────


def _obs(oid, media_id, name, *, human=False, deployment="d1"):
    return {
        "id": oid,
        "deployment_id": deployment,
        "media_id": media_id,
        "observation_type": "animal",
        "scientific_name": name,
        "taxon_id": None,
        "source_type": "human" if human else "ai",
        "review_status": "human_reviewed" if human else "ai_reviewed",
        "classification_method": "human" if human else "machine",
        "created_at": "2026-11-01T00:00:00+00:00",
        "updated_at": "2026-11-01T00:00:00+00:00",
    }


def _vector(v: np.ndarray) -> str:
    return "[" + ",".join(str(float(x)) for x in v) + "]"


def _project(checks=(), extra_obs=()):
    """Eight rats, eight mice and one rat that looks like a mouse (m16), in project p1."""
    vectors = np.vstack([_around(_axis(0), 8), _around(_axis(1), 8), _around(_axis(1), 1)])
    names = ["Rattus rattus"] * 8 + ["Mus musculus"] * 8 + ["Rattus rattus"]
    media = [f"m{i}" for i in range(17)]
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": "org-1"}, {"id": "p2", "organisation_id": "org-1"}],
            "deployments": [{"id": "d1", "project_id": "p1"}, {"id": "d2", "project_id": "p2"}],
            "media": [{"id": m, "deployment_id": "d1"} for m in media],
            "observations": [_obs(f"o{i}", m, n, human=True) for i, (m, n) in enumerate(zip(media, names))] + list(extra_obs),
            "media_embeddings": [
                {"media_id": m, "deployment_id": "d1", "embedding_run_id": "r1", "embedding": _vector(v), "embedding_model": "dinov3-vith"}
                for m, v in zip(media, vectors)
            ],
            "label_checks": [dict(c) for c in checks],
            "taxa": [],
            "user_roles": [],
        }
    )


def _check(cid, observation_id, kind, outcome=None, label="Rattus rattus", run="r1"):
    return {
        "id": cid,
        "observation_id": observation_id,
        "project_id": "p1",
        "kind": kind,
        "score": 0.9,
        "reference": "confirmed",
        "embedding_run_id": run,
        "label_scientific_name": label,
        "outcome": outcome,
        "checked_by": "u9" if outcome == "checked" else None,
    }


def _rows(db):
    return {(r["observation_id"], r["kind"]): r for r in db.tables["label_checks"]}


def test_a_recompute_writes_the_flags():
    db = _project()
    result = runs.recompute_project(db, "p1", TUNE)
    rows = _rows(db)
    assert set(rows) == {("o16", LOOK_ALIKE), ("o16", ODD_ONE_OUT)}
    assert rows[("o16", LOOK_ALIKE)]["other_scientific_name"] == "Mus musculus" and rows[("o16", LOOK_ALIKE)]["project_id"] == "p1"
    assert result.flagged == 2 and result.written == 2
    # Again: the same two rows, updated in place.
    runs.recompute_project(db, "p1", TUNE)
    assert len(db.tables["label_checks"]) == 2


def test_an_open_flag_no_longer_flagged_is_relabelled_or_deleted():
    # o3 was flagged as a rat and is still one: deleted. o5 was flagged as a mouse and a person
    # has since made the photo a rat: relabelled.
    db = _project(checks=[_check("k3", "o3", ODD_ONE_OUT), _check("k5", "o5", LOOK_ALIKE, label="Mus musculus")])
    result = runs.recompute_project(db, "p1", TUNE)
    rows = _rows(db)
    assert ("o3", ODD_ONE_OUT) not in rows
    assert rows[("o5", LOOK_ALIKE)]["outcome"] == "relabelled"
    assert (result.relabelled, result.deleted) == (1, 1)


def test_a_photo_relabelled_through_a_new_row_marks_the_old_flag_relabelled():
    # The flag sits on the AI row a1 of m3; a person's row now names m3 a mouse.
    ai = {**_obs("a1", "m3", "Rattus rattus"), "created_at": "2026-10-01T00:00:00+00:00"}
    db = _project(checks=[_check("k1", "a1", LOOK_ALIKE)], extra_obs=[ai])
    db.tables["observations"] = [o for o in db.tables["observations"] if o["id"] != "o3"] + [_obs("o3", "m3", "Mus musculus", human=True)]
    runs.recompute_project(db, "p1", TUNE)
    assert _rows(db)[("a1", LOOK_ALIKE)]["outcome"] == "relabelled"


def test_a_checked_flag_stays_checked_until_its_fingerprint_changes():
    db = _project(checks=[_check("k1", "o16", LOOK_ALIKE, outcome="checked")])
    runs.recompute_project(db, "p1", TUNE)
    assert _rows(db)[("o16", LOOK_ALIKE)]["outcome"] == "checked"

    db = _project(checks=[_check("k1", "o16", LOOK_ALIKE, outcome="checked", run="r0")])
    runs.recompute_project(db, "p1", TUNE)
    row = _rows(db)[("o16", LOOK_ALIKE)]
    assert (row["outcome"], row["checked_by"], row["embedding_run_id"]) == (None, None, "r1")


def test_the_summary_counts_open_checked_and_relabelled():
    db = _project(checks=[_check("k1", "o1", LOOK_ALIKE), _check("k2", "o2", ODD_ONE_OUT, "checked"), _check("k3", "o3", LOOK_ALIKE, "relabelled")])
    assert runs.summary(db, "p1") == {"open": {LOOK_ALIKE: 1}, "checked": 1, "relabelled": 1, "computed_at": None}


def test_projects_with_changed_labels_since():
    db = _project()
    db.tables["observations"][0]["updated_at"] = "2026-11-27T10:00:00+00:00"
    assert runs.projects_with_changed_labels(db, "2026-11-27T00:00:00+00:00") == ["p1"]
    assert runs.projects_with_changed_labels(db, "2026-11-28T00:00:00+00:00") == []


# ── When ─────────────────────────────────────────────────────────────


def test_the_nightly_runs_in_its_hour_once_a_day(monkeypatch):
    monkeypatch.setattr(settings, "LABEL_CHECK_NIGHTLY_HOUR_UTC", 14)
    at = datetime(2026, 11, 27, 14, 5, tzinfo=timezone.utc)
    assert jobs.nightly_due(at, None)
    assert jobs.nightly_due(at, "2026-11-26T14:00:00+00:00")
    assert not jobs.nightly_due(at, "2026-11-27T14:00:00+00:00")
    assert not jobs.nightly_due(at.replace(hour=13), None)


async def test_nothing_is_queued_with_the_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", False)
    monkeypatch.setattr(settings, "LABEL_CHECK_NIGHTLY_HOUR_UTC", 14)
    queued = []

    async def fake_queue(project_id, user_id=None):
        queued.append(project_id)
        return "j"

    monkeypatch.setattr(jobs, "queue_project", fake_queue)
    await jobs.queue_after_brain_run(["d1"])
    assert await jobs.maybe_queue_nightly(datetime(2026, 11, 27, 14, 5, tzinfo=timezone.utc)) is None
    assert queued == []


async def test_a_brain_run_queues_its_projects(monkeypatch):
    monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", True)
    db = _project()
    monkeypatch.setattr("app.services.supabase_client.create_service_client", db.client)
    queued = []

    async def fake_queue(project_id, user_id=None):
        queued.append(project_id)
        return "j"

    monkeypatch.setattr(jobs, "queue_project", fake_queue)
    await jobs.queue_after_brain_run(["d1", "d1"])
    assert queued == ["p1"]


# ── Routes ───────────────────────────────────────────────────────────

MEMBER = {"role": "project_member", "scope_type": "project", "scope_id": "p1"}
VIEWER = {"role": "project_viewer", "scope_type": "project", "scope_id": "p1"}


@pytest.fixture
def api(monkeypatch):
    def make(db, roles):
        db.tables["user_roles"] = [{"user_id": "u1", "is_active": True, **r} for r in roles]
        monkeypatch.setattr(router, "create_service_client", db.client)
        monkeypatch.setattr(authz, "create_service_client", db.client)
        monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", True)
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai", app_metadata={})
        return TestClient(app)

    return make


def test_a_viewer_reads_the_summary(api):
    db = _project(checks=[_check("k1", "o1", LOOK_ALIKE)])
    r = api(db, [VIEWER]).get("/api/label-checks/p1")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["open"] == {LOOK_ALIKE: 1}


def test_a_recompute_needs_a_member(api, monkeypatch):
    queued = []

    async def fake_queue(project_id, user_id=None):
        queued.append((project_id, user_id))
        return "job-1"

    monkeypatch.setattr(router, "queue_project", fake_queue)
    assert api(_project(), [VIEWER]).post("/api/label-checks/p1/recompute").status_code == 403
    r = api(_project(), [MEMBER]).post("/api/label-checks/p1/recompute")
    assert r.json()["data"] == {"job_id": "job-1", "status": "queued", "project_id": "p1"}
    assert queued == [("p1", "u1")]


def test_the_routes_answer_feature_disabled_with_the_flag_off(api, monkeypatch):
    client = api(_project(), [MEMBER])
    monkeypatch.setattr(settings, "FF_LABEL_CHECKS_ENABLED", False)
    assert client.get("/api/label-checks/p1").json()["error"]["code"] == "FEATURE_DISABLED"
    assert client.post("/api/label-checks/p1/recompute").json()["error"]["code"] == "FEATURE_DISABLED"
