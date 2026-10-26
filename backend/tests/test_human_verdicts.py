# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""One human verdict row per photo from the Wildlife Brain writes (UI audit decision 2).

Cluster confirm labels only the members nobody has labelled and its undo takes back only its own
rows; the batch review adds a photo's human row or updates the one it has. The routes refuse a
project viewer and the demo account, cap a batch at 500 photos, and page every read.
"""

import copy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import authz
from app.config import settings
from app.dependencies import get_current_user
from app.domain.human_verdicts import ClusterStateError, Label, Reviewer, confirm_cluster, has_human_verdict, review_photos, unconfirm_cluster
from app.routers import brain
from tests.fake_postgrest import FakeDB

ME = Reviewer(id="u1", email="u@ww.ai")
RAT = Label(taxon_id="t-rat", scientific_name="Rattus rattus", vernacular_name="Ship rat")
OPEN = {
    "id": "c1",
    "deployment_id": "d1",
    "embedding_run_id": "r1",
    "cluster_id": 3,
    "review_state": "open",
    "taxon_id": None,
    "scientific_name": None,
    "confirmed_by": None,
    "confirmed_at": None,
}


def _ai(oid, media_id, name="Mus musculus", **extra):
    return {
        "id": oid,
        "deployment_id": "d1",
        "media_id": media_id,
        "observation_type": "animal",
        "scientific_name": name,
        "source_type": "ai",
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        **extra,
    }


def _human(oid, media_id, name="Felis catus", **extra):
    return {
        "id": oid,
        "deployment_id": "d1",
        "media_id": media_id,
        "observation_type": "animal",
        "scientific_name": name,
        "source_type": "human",
        "review_status": "human_reviewed",
        "classification_method": "human",
        "annotator_id": "someone",
        **extra,
    }


def _db(members=("m1", "m2", "m3", "m4"), observations=(), assignment=None, roles=(), media=None):
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": "org-1"}, {"id": "p2", "organisation_id": "org-1"}],
            "deployments": [{"id": "d1", "project_id": "p1"}, {"id": "d2", "project_id": "p2"}],
            "media": media if media is not None else [{"id": m, "deployment_id": "d1"} for m in members],
            "cluster_assignments": [assignment or OPEN],
            "media_embeddings": [{"media_id": m, "deployment_id": "d1", "embedding_run_id": "r1", "cluster_id": 3} for m in members],
            "observations": [dict(o) for o in observations],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


def _rows(db, media_id):
    return [o for o in db.tables["observations"] if o.get("media_id") == media_id]


def _human_rows(db, media_id):
    return [o for o in _rows(db, media_id) if o.get("source_type") == "human"]


# ── The verdict rule ─────────────────────────────────────────────────


def test_a_human_verdict_is_a_persons_row_or_a_row_they_reviewed():
    assert has_human_verdict([]) is False
    assert has_human_verdict([_ai("a", "m1")]) is False
    assert has_human_verdict([_human("h", "m1")]) is True
    assert has_human_verdict([_ai("a", "m1", review_status="human_reviewed")]) is True
    assert has_human_verdict([_ai("a", "m1", source_type="consensus", review_status="consensus_approved")]) is True
    assert has_human_verdict([{"id": "x", "media_id": "m1", "classification_method": "human"}]) is True


# ── confirm_cluster ──────────────────────────────────────────────────


def test_confirm_labels_only_the_members_nobody_has_labelled():
    observations = [
        _ai("a1", "m1"),
        _human("h2", "m2"),  # a person's own label
        _ai("a3", "m3", review_status="human_reviewed"),  # an AI row a person confirmed
    ]
    db = _db(observations=observations)
    result = confirm_cluster(db, "c1", RAT, ME)

    assert result is not None
    assert result.skipped == 2
    assert sorted(o["media_id"] for o in db.tables["observations"] if o["id"] in result.observation_ids) == ["m1", "m4"]
    for m in ("m1", "m4"):
        [row] = _human_rows(db, m)
        assert row["id"] in result.observation_ids
        assert row["scientific_name"] == "Rattus rattus" and row["taxon_id"] == "t-rat" and row["vernacular_name"] == "Ship rat"
        assert row["review_status"] == "human_reviewed" and row["classification_method"] == "human"
        assert (row["embedding_run_id"], row["cluster_id"]) == ("r1", 3)
        assert (row["reviewer_id"], row["annotator_id"], row["classified_by"]) == ("u1", "u1", "u@ww.ai")
    # The person's label and every AI row are as they were.
    assert _human_rows(db, "m2") == [observations[1]]
    assert [o for o in db.tables["observations"] if o["source_type"] == "ai"] == [observations[0], observations[2]]
    [cluster] = db.tables["cluster_assignments"]
    assert (cluster["review_state"], cluster["taxon_id"], cluster["scientific_name"], cluster["confirmed_by"]) == (
        "confirmed",
        "t-rat",
        "Rattus rattus",
        "u1",
    )
    assert cluster["confirmed_at"]


def test_confirm_labels_every_member_past_1000_in_chunks_of_50():
    members = [f"m{i:05d}" for i in range(2345)]
    db = _db(members=members)
    result = confirm_cluster(db, "c1", RAT, ME)

    assert result is not None and result.skipped == 0
    assert len(result.observation_ids) == len(members)
    assert sorted(o["media_id"] for o in db.tables["observations"]) == members
    inserts = [w.rows for w in db.writes if w.mode == "insert"]
    assert max(inserts) == 50 and sum(inserts) == len(members)


def test_confirm_refuses_a_confirmed_cluster_and_writes_nothing():
    db = _db(assignment={**OPEN, "review_state": "confirmed", "confirmed_at": "2026-10-25T00:00:00+00:00"})
    with pytest.raises(ClusterStateError) as exc:
        confirm_cluster(db, "c1", RAT, ME)
    assert exc.value.code == "ALREADY_CONFIRMED"
    assert db.writes == []


def test_confirm_of_an_unknown_cluster_is_none():
    assert confirm_cluster(_db(), "nope", RAT, ME) is None


# ── unconfirm_cluster ────────────────────────────────────────────────


def test_unconfirm_deletes_only_the_rows_its_confirm_wrote():
    db = _db(observations=[_ai("a1", "m1"), _human("h2", "m2")])
    confirm_cluster(db, "c1", RAT, ME)
    others = [
        _human("other-run", "m1", "Rattus rattus", embedding_run_id="r0", cluster_id=3),
        _human("other-cluster", "m1", "Rattus rattus", embedding_run_id="r1", cluster_id=4),
        _ai("ai-with-cluster", "m1", "Rattus rattus", embedding_run_id="r1", cluster_id=3),
    ]
    db.tables["observations"] += [dict(o) for o in others]
    # A person relabelled m4 after the confirm: that label is theirs now.
    [m4] = _human_rows(db, "m4")
    m4.update(scientific_name="Rattus norvegicus", taxon_id="t-norway")

    result = unconfirm_cluster(db, "c1")

    assert result is not None
    assert (result.deleted, result.kept) == (2, 1)  # m1 and m3 go, m4 stays
    remaining = {o["id"] for o in db.tables["observations"]}
    assert remaining == {"a1", "h2", m4["id"], "other-run", "other-cluster", "ai-with-cluster"}
    [cluster] = db.tables["cluster_assignments"]
    assert {k: cluster[k] for k in ("review_state", "taxon_id", "scientific_name", "confirmed_by", "confirmed_at")} == {
        "review_state": "open",
        "taxon_id": None,
        "scientific_name": None,
        "confirmed_by": None,
        "confirmed_at": None,
    }


def test_confirm_then_unconfirm_leaves_the_photos_as_they_were():
    observations = [_ai("a1", "m1"), _human("h2", "m2"), _ai("a3", "m3", review_status="human_reviewed")]
    db = _db(observations=observations)
    before = copy.deepcopy(db.tables["observations"])
    confirm_cluster(db, "c1", RAT, ME)
    unconfirm_cluster(db, "c1")
    assert db.tables["observations"] == before


def test_unconfirm_pages_past_1000_rows():
    members = [f"m{i:05d}" for i in range(1500)]
    db = _db(members=members)
    confirm_cluster(db, "c1", RAT, ME)
    result = unconfirm_cluster(db, "c1")
    assert result is not None and result.deleted == 1500
    assert db.tables["observations"] == []


def test_unconfirm_refuses_an_open_cluster():
    db = _db()
    with pytest.raises(ClusterStateError) as exc:
        unconfirm_cluster(db, "c1")
    assert exc.value.code == "NOT_CONFIRMED"
    assert db.writes == []


# ── review_photos ────────────────────────────────────────────────────


def test_reassign_gives_each_photo_one_human_row():
    observations = [
        _ai("a1", "p1"),
        _ai("a2", "p2"),
        _human("h2", "p2"),
        _human("h3a", "p3"),  # the bulk label used to add a row each time
        _human("h3b", "p3", "Sus scrofa"),
    ]
    media = [{"id": p, "deployment_id": "d1"} for p in ("p1", "p2", "p3", "p5")] + [{"id": "p4", "deployment_id": "d1", "deleted_at": "2026-10-01"}]
    db = _db(observations=observations, media=media)
    result = review_photos(db, ["p1", "p2", "p3", "p4", "p5", "p1"], ["p1", "p2", "p3", "p4"], "reassign", RAT, ME)

    assert (result.created, result.updated) == (1, 2)
    assert result.skipped == [{"media_id": "p5", "reason": "not_writable"}, {"media_id": "p4", "reason": "not_found"}]
    [new] = _human_rows(db, "p1")
    assert result.observation_ids == [new["id"], "h2", "h3a", "h3b"]
    for p in ("p1", "p2", "p3"):
        assert {(o["scientific_name"], o["review_status"], o["reviewer_id"]) for o in _human_rows(db, p)} == {
            ("Rattus rattus", "human_reviewed", "u1")
        }
    assert _human_rows(db, "p2")[0]["annotator_id"] == "someone"
    assert [o for o in db.tables["observations"] if o["source_type"] == "ai"] == [observations[0], observations[1]]
    assert _rows(db, "p4") == [] and _rows(db, "p5") == []


def test_reviewing_again_updates_the_row_instead_of_adding_one():
    db = _db(media=[{"id": "p1", "deployment_id": "d1"}])
    review_photos(db, ["p1"], ["p1"], "reassign", RAT, ME)
    again = review_photos(db, ["p1"], ["p1"], "reassign", Label(scientific_name="Mus musculus"), ME)
    assert (again.created, again.updated) == (0, 1)
    [row] = _human_rows(db, "p1")
    assert row["scientific_name"] == "Mus musculus"


def test_expert_without_a_species_keeps_a_persons_label():
    db = _db(observations=[_human("h2", "p2")], media=[{"id": p, "deployment_id": "d1"} for p in ("p1", "p2")])
    result = review_photos(db, ["p1", "p2"], ["p1", "p2"], "expert", Label(), ME)

    assert (result.created, result.updated) == (1, 1)
    [kept] = _human_rows(db, "p2")
    assert (kept["scientific_name"], kept["review_status"], kept["reviewer_id"]) == ("Felis catus", "expert_reviewed", "u1")
    # A photo with no human row gets the per-photo route's row: an animal, no species, expert_reviewed.
    [new] = _human_rows(db, "p1")
    assert (new["observation_type"], new["scientific_name"], new["review_status"]) == ("animal", None, "expert_reviewed")


def test_review_writes_in_chunks_of_50():
    photos = [f"p{i:04d}" for i in range(120)]
    db = _db(media=[{"id": p, "deployment_id": "d1"} for p in photos])
    result = review_photos(db, photos, photos, "reassign", RAT, ME)
    assert result.created == 120
    assert [w.rows for w in db.writes if w.mode == "insert"] == [50, 50, 20]


# ── Routes ───────────────────────────────────────────────────────────

MEMBER = {"role": "project_member", "scope_type": "project", "scope_id": "p1"}
VIEWER = {"role": "project_viewer", "scope_type": "project", "scope_id": "p1"}


@pytest.fixture
def api(monkeypatch):
    def make(db, demo=False):
        monkeypatch.setattr(brain, "create_service_client", db.client)
        monkeypatch.setattr(authz, "create_service_client", db.client)
        app = FastAPI()
        app.include_router(brain.router)
        user = SimpleNamespace(id="u1", email="u@ww.ai", app_metadata={"is_demo": demo})
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return make


def test_confirm_route_returns_the_new_ids_and_the_skipped_count(api):
    db = _db(observations=[_human("h2", "m2")], roles=[MEMBER])
    r = api(db).post("/api/brain/clusters/c1/confirm", json={"taxon_id": "t-rat", "scientific_name": "Rattus rattus"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["observations_created"] == 3 and len(data["observation_ids"]) == 3 and data["skipped"] == 1


def test_confirm_route_answers_already_confirmed(api):
    db = _db(roles=[MEMBER], assignment={**OPEN, "review_state": "confirmed", "confirmed_at": "2026-10-25T00:00:00+00:00"})
    r = api(db).post("/api/brain/clusters/c1/confirm", json={"scientific_name": "Rattus rattus"})
    assert r.json()["error"]["code"] == "ALREADY_CONFIRMED"


def test_unconfirm_route_reopens_the_cluster(api):
    db = _db(roles=[MEMBER])
    client = api(db)
    client.post("/api/brain/clusters/c1/confirm", json={"scientific_name": "Rattus rattus"})
    r = client.post("/api/brain/clusters/c1/unconfirm")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"cluster_assignment_id": "c1", "observations_deleted": 4, "kept": 0}
    assert db.tables["cluster_assignments"][0]["review_state"] == "open"
    assert api(db).post("/api/brain/clusters/c1/unconfirm").json()["error"]["code"] == "NOT_CONFIRMED"


@pytest.mark.parametrize("path", ["/api/brain/clusters/c1/confirm", "/api/brain/clusters/c1/unconfirm"])
def test_cluster_writes_refuse_a_viewer_and_the_demo(api, path):
    body = {"scientific_name": "Rattus rattus"}
    assert api(_db(roles=[VIEWER])).post(path, json=body).status_code == 403
    assert api(_db(roles=[MEMBER]), demo=True).post(path, json=body).status_code == 403


def test_batch_route_skips_the_photos_the_caller_may_not_change(api):
    media = [{"id": "p1", "deployment_id": "d1"}, {"id": "q1", "deployment_id": "d2"}]
    db = _db(roles=[MEMBER, {**VIEWER, "scope_id": "p2"}], media=media)
    r = api(db).post("/api/brain/review/batch", json={"media_ids": ["p1", "q1"], "decision": "reassign", "scientific_name": "Rattus rattus"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert (data["created"], data["updated"], data["skipped"]) == (1, 0, [{"media_id": "q1", "reason": "not_writable"}])
    assert len(data["observation_ids"]) == 1
    assert _rows(db, "q1") == []


def test_batch_route_refuses_a_viewer_and_the_demo(api):
    body = {"media_ids": ["p1"], "decision": "expert"}
    media = [{"id": "p1", "deployment_id": "d1"}]
    assert api(_db(roles=[VIEWER], media=media)).post("/api/brain/review/batch", json=body).status_code == 403
    assert api(_db(roles=[MEMBER], media=media), demo=True).post("/api/brain/review/batch", json=body).status_code == 403


def test_batch_route_takes_at_most_500_photos(api):
    client = api(_db(roles=[MEMBER]))
    body = {"decision": "expert"}
    assert client.post("/api/brain/review/batch", json={**body, "media_ids": [f"x{i}" for i in range(501)]}).status_code == 422
    assert client.post("/api/brain/review/batch", json={**body, "media_ids": []}).status_code == 422
    # 500 unknown photos pass validation and are refused as not writable.
    assert client.post("/api/brain/review/batch", json={**body, "media_ids": [f"x{i}" for i in range(500)]}).status_code == 403


def test_batch_reassign_needs_a_species(api):
    r = api(_db(roles=[MEMBER])).post("/api/brain/review/batch", json={"media_ids": ["m1"], "decision": "reassign"})
    assert r.status_code == 422


def test_per_photo_review_updates_the_photos_human_row(api):
    db = _db(observations=[_human("h1", "m1")], roles=[MEMBER])
    r = api(db).post("/api/brain/review/m1", json={"decision": "reassign", "scientific_name": "Rattus rattus"})
    assert r.json()["data"] == {"media_id": "m1", "decision": "reassign", "review_status": "human_reviewed"}
    [row] = _human_rows(db, "m1")
    assert row["id"] == "h1" and row["scientific_name"] == "Rattus rattus"


def test_brain_writes_answer_feature_disabled_with_the_flag_off(api, monkeypatch):
    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", False)
    db = _db(roles=[MEMBER])
    client = api(db)
    assert client.post("/api/brain/clusters/c1/unconfirm").json()["error"]["code"] == "FEATURE_DISABLED"
    batch = client.post("/api/brain/review/batch", json={"media_ids": ["m1"], "decision": "expert"})
    assert batch.json()["error"]["code"] == "FEATURE_DISABLED"
    assert db.writes == []
