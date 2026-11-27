# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which species a Wildlife Brain group mixes (#450): broader and narrower names are not a mix,
each label counts the members a confirm would relabel, and the route reads only clusters of the
deployment the caller may read."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import authz
from app.config import settings
from app.dependencies import get_current_user
from app.domain import cluster_labels as cl
from app.domain.taxon_lineage import Named, contains, mixes_species, related
from app.routers import brain
from tests.fake_postgrest import FakeDB

TAXA = {
    "t-rattus": {"id": "t-rattus", "scientific_name": "Rattus", "rank": "genus", "genus": "Rattus"},
    "t-rat": {"id": "t-rat", "scientific_name": "Rattus rattus", "rank": "species", "genus": "Rattus", "species": "rattus"},
    "t-norway": {"id": "t-norway", "scientific_name": "Rattus norvegicus", "rank": "species", "genus": "Rattus", "species": "Rattus norvegicus"},
    "t-muridae": {"id": "t-muridae", "scientific_name": "Muridae", "rank": "family", "family": "Muridae"},
    "t-mouse": {"id": "t-mouse", "scientific_name": "Mus musculus", "rank": "species", "family": "Muridae", "genus": "Mus", "species": "musculus"},
    "t-aves": {"id": "t-aves", "scientific_name": "Aves", "rank": "class", "class": "Aves"},
    "t-rodentia": {"id": "t-rodentia", "scientific_name": "Rodentia", "rank": "order", "order_name": "Rodentia"},
    "t-rat-full": {
        "id": "t-rat-full",
        "scientific_name": "Rattus rattus",
        "rank": "species",
        "order_name": "Rodentia",
        "family": "Muridae",
        "genus": "Rattus",
        "species": "rattus",
    },
}

RAT = Named("Rattus rattus", "t-rat")
NORWAY = Named("Rattus norvegicus", "t-norway")
RATTUS = Named("Rattus", "t-rattus")
MOUSE = Named("Mus musculus", "t-mouse")
MURIDAE = Named("Muridae", "t-muridae")


# ── taxon_lineage ────────────────────────────────────────────────────


def test_a_genus_contains_its_species_by_lineage_or_by_name():
    assert contains(RATTUS, RAT, TAXA) and contains(RATTUS, NORWAY, TAXA)
    assert not contains(RAT, RATTUS, TAXA)
    # No taxa rows: a name with one more word is narrower, a name sharing only a prefix is not.
    assert contains(Named("Rattus"), Named("rattus  rattus"), {})
    assert not contains(Named("Rattus"), Named("Rattus rattus alexandrinus"), {})
    assert not contains(Named("Ratt"), Named("Rattus rattus"), {})


def test_a_family_contains_a_species_through_its_taxa_row():
    assert contains(MURIDAE, MOUSE, TAXA)
    assert not contains(Named("Aves", "t-aves"), MOUSE, TAXA)
    # Without the narrow taxon's row the family cannot be placed, so it is a different name.
    assert not contains(MURIDAE, Named("Mus musculus"), TAXA)


def test_an_order_contains_a_species_through_its_order_name_column():
    # taxa.rank holds 'order' and the narrower row names it in order_name (34a_taxa.sql).
    rodentia, rat = Named("Rodentia", "t-rodentia"), Named("Rattus rattus", "t-rat-full")
    assert contains(rodentia, rat, TAXA)
    assert not mixes_species([rodentia, rat], TAXA)


def test_two_species_mix_but_a_broader_name_does_not():
    assert related(RAT, Named("Rattus rattus"), TAXA)
    assert mixes_species([RAT, NORWAY], TAXA)
    assert not mixes_species([RATTUS, RAT], TAXA)
    assert not mixes_species([MURIDAE, MOUSE], TAXA)
    assert mixes_species([RATTUS, RAT, MOUSE], TAXA)
    assert not mixes_species([RAT], TAXA) and not mixes_species([], TAXA)


# ── cluster_labels ───────────────────────────────────────────────────

C1 = {"id": "c1", "deployment_id": "d1", "embedding_run_id": "r1", "cluster_id": 3, "review_state": "open"}
C2 = {"id": "c2", "deployment_id": "d1", "embedding_run_id": "r1", "cluster_id": 4, "review_state": "open"}
OTHER = {"id": "c9", "deployment_id": "d2", "embedding_run_id": "r9", "cluster_id": 3, "review_state": "open"}


def _ai(oid, media_id, name, taxon=None, **extra):
    return {
        "id": oid,
        "deployment_id": "d1",
        "media_id": media_id,
        "observation_type": "animal",
        "scientific_name": name,
        "vernacular_name": None,
        "taxon_id": taxon,
        "source_type": "ai",
        "review_status": "ai_reviewed",
        "classification_method": "machine",
        "created_at": "2026-11-01T00:00:00+00:00",
        **extra,
    }


def _human(oid, media_id, name, taxon=None):
    return {
        **_ai(oid, media_id, name, taxon),
        "source_type": "human",
        "review_status": "human_reviewed",
        "classification_method": "human",
        "created_at": "2026-11-02T00:00:00+00:00",
    }


def _db(members, observations, assignments=(C1, C2, OTHER), roles=()):
    return FakeDB(
        {
            "projects": [{"id": "p1", "organisation_id": "org-1"}, {"id": "p2", "organisation_id": "org-1"}],
            "deployments": [{"id": "d1", "project_id": "p1"}, {"id": "d2", "project_id": "p2"}],
            "cluster_assignments": [dict(a) for a in assignments],
            "media_embeddings": [{"media_id": m, "deployment_id": d, "embedding_run_id": r, "cluster_id": c} for m, d, r, c in members],
            "observations": [dict(o) for o in observations],
            "taxa": [dict(t) for t in TAXA.values()],
            "user_roles": [{"user_id": "u1", "is_active": True, **r} for r in roles],
        }
    )


def _members(cluster_id, *media, deployment="d1", run="r1"):
    return [(m, deployment, run, cluster_id) for m in media]


def test_a_cluster_of_two_species_is_mixed_and_counts_what_a_confirm_relabels():
    members = _members(3, "m1", "m2", "m3", "m4", "m5") + _members(4, "n1", "n2")
    observations = [
        _ai("a1", "m1", "Rattus rattus", "t-rat", vernacular_name="Ship rat"),
        _ai("a2", "m2", "Rattus rattus", "t-rat"),
        _ai("a3", "m3", "Rattus norvegicus", "t-norway"),
        _human("h3", "m3", "Rattus rattus", "t-rat"),  # a person's label wins the verdict
        _ai("a4", "m4", "Rattus norvegicus", "t-norway"),
        _ai("a5", "m5", None, observation_type="blank"),  # not a species
        _ai("b1", "n1", "Rattus", "t-rattus"),
        _ai("b2", "n2", "Rattus rattus", "t-rat"),
        _ai("x1", "m4", "Rattus rattus", "t-rat", deleted_at="2026-11-03"),  # deleted, ignored
    ]
    result = cl.cluster_labels(_db(members, observations), "d1", ["c2", "c1"])

    assert [c.id for c in result] == ["c2", "c1"]
    c2, c1 = result
    assert not c2.mixed
    assert c1.mixed
    assert [(lc.scientific_name, lc.vernacular_name, lc.taxon_id, lc.photos, lc.unreviewed) for lc in c1.labels] == [
        ("Rattus rattus", "Ship rat", "t-rat", 3, 2),
        ("Rattus norvegicus", None, "t-norway", 1, 1),
    ]


def test_labels_without_a_taxon_compare_by_name():
    members = _members(3, "m1", "m2", "m3")
    observations = [_ai("a1", "m1", "Rattus"), _ai("a2", "m2", "rattus rattus"), _ai("a3", "m3", "Rattus Rattus")]
    [c1] = cl.cluster_labels(_db(members, observations), "d1", ["c1"])
    assert not c1.mixed
    assert [(lc.scientific_name, lc.photos) for lc in c1.labels] == [("rattus rattus", 2), ("Rattus", 1)]


def test_a_cluster_of_another_deployment_or_unknown_is_left_out():
    members = _members(3, "m1") + _members(3, "z1", deployment="d2", run="r9")
    observations = [_ai("a1", "m1", "Rattus rattus"), {**_ai("z", "z1", "Mus musculus"), "deployment_id": "d2"}]
    result = cl.cluster_labels(_db(members, observations), "d1", ["c9", "nope", "c1", "c1"])
    assert [c.id for c in result] == ["c1"]


def test_a_cluster_with_no_labelled_members_has_no_labels():
    [c1] = cl.cluster_labels(_db(_members(3, "m1"), []), "d1", ["c1"])
    assert (c1.mixed, c1.labels) == (False, [])


def _reads(db, monkeypatch):
    """Counts the reads of observations."""
    reads = []
    table = db.table

    def counted(name):
        if name == "observations":
            reads.append(name)
        return table(name)

    monkeypatch.setattr(db, "table", counted)
    return reads


def test_a_big_cluster_reads_the_deployments_rows_whole(monkeypatch):
    media = [f"m{i:04d}" for i in range(300)]
    db = _db(_members(3, *media), [_ai(f"a{i}", m, "Rattus rattus") for i, m in enumerate(media)])
    reads = _reads(db, monkeypatch)
    [c1] = cl.cluster_labels(db, "d1", ["c1"])
    assert c1.labels[0].photos == 300
    # One count, then one page of 300 rows instead of six chunks of 50 photos.
    assert len(reads) == 2


def test_a_small_cluster_of_a_big_deployment_reads_its_members_in_chunks(monkeypatch):
    others = [_ai(f"o{i}", f"x{i}", "Mus musculus") for i in range(2500)]
    db = _db(_members(3, "m1", "m2"), [_ai("a1", "m1", "Rattus rattus"), _ai("a2", "m2", "Mus musculus"), *others])
    reads = _reads(db, monkeypatch)
    [c1] = cl.cluster_labels(db, "d1", ["c1"])
    assert c1.mixed and len(reads) == 2


# ── Route ────────────────────────────────────────────────────────────

VIEWER = {"role": "project_viewer", "scope_type": "project", "scope_id": "p1"}


@pytest.fixture
def api(monkeypatch):
    def make(db):
        monkeypatch.setattr(brain, "create_service_client", db.client)
        monkeypatch.setattr(authz, "create_service_client", db.client)
        app = FastAPI()
        app.include_router(brain.router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai", app_metadata={})
        return TestClient(app)

    return make


def test_the_route_answers_each_clusters_labels_to_a_viewer(api):
    members = _members(3, "m1", "m2")
    db = _db(members, [_ai("a1", "m1", "Rattus rattus", "t-rat"), _ai("a2", "m2", "Mus musculus", "t-mouse")], roles=[VIEWER])
    r = api(db).get("/api/brain/clusters/d1/labels", params={"ids": ["c1", "c9"]})
    assert r.status_code == 200, r.text
    [c1] = r.json()["data"]["clusters"]
    assert c1["id"] == "c1" and c1["mixed"] is True
    assert c1["labels"][0] == {"scientific_name": "Mus musculus", "vernacular_name": None, "taxon_id": "t-mouse", "photos": 1, "unreviewed": 1}


def test_the_route_hides_a_deployment_the_caller_cannot_read(api):
    db = _db(_members(3, "m1"), [], roles=[{**VIEWER, "scope_id": "p2"}])
    assert api(db).get("/api/brain/clusters/d1/labels", params={"ids": ["c1"]}).status_code == 404


def test_the_route_takes_1_to_50_clusters(api):
    client = api(_db([], [], roles=[VIEWER]))
    assert client.get("/api/brain/clusters/d1/labels").status_code == 422
    assert client.get("/api/brain/clusters/d1/labels", params={"ids": [f"c{i}" for i in range(51)]}).status_code == 422
    assert client.get("/api/brain/clusters/d1/labels", params={"ids": [f"c{i}" for i in range(50)]}).status_code == 200


def test_the_route_answers_feature_disabled_with_the_flag_off(api, monkeypatch):
    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", False)
    r = api(_db([], [], roles=[VIEWER])).get("/api/brain/clusters/d1/labels", params={"ids": ["c1"]})
    assert r.json()["error"]["code"] == "FEATURE_DISABLED"
