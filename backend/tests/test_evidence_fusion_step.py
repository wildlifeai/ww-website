# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""EvidenceFusionStep: consensus row (report section 7), idempotence, media_evidence stub, gating and pipeline placement (client mocked)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from app.config import settings
from app.domain.pipeline import EVIDENCE_FUSION_VERSION, EvidenceFusionStep, build_consensus_observation, fusion_evidence_signals
from app.jobs.definitions import build_pipeline_steps
from app.schemas.pipeline import PipelineStepType
from app.services import media_evidence as me

DEP = "d0000000-0000-4000-8000-000000000001"
SN = "speciesnet-v4.0.1a"
GEM = "gemini-3.1-flash-lite"


class _Missing(Exception):
    code = "PGRST205"


class _Query:
    """Records the call chain; execute() answers from the fake's tables."""

    def __init__(self, fake, table):
        self.fake, self.table, self.op, self.filters, self.payload = fake, table, "select", [], None
        self.columns = ""

    def select(self, cols="", *_a, **_k):
        self.op, self.columns = "select", cols
        return self

    def delete(self):
        self.op = "delete"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict=""):
        self.op, self.payload = "upsert", (rows, on_conflict)
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def limit(self, n):
        self.filters.append(("limit", n))
        return self

    def execute(self):
        return self.fake.execute(self)


class _R:
    def __init__(self, data):
        self.data = data


class _Fake:
    def __init__(self, media, observations, evidence_table=True, evidence_rows=None):
        self.media, self.observations, self.evidence_table = media, observations, evidence_table
        self.evidence_rows = evidence_rows or []
        self.deletes, self.inserted, self.upserts = [], [], []
        # delete_superseded_ai_observations removes crops best-effort before the rows.
        self.storage = MagicMock()

    def table(self, name):
        return _Query(self, name)

    def _ids(self, q, col):
        return next((f[2] for f in q.filters if f[0] == "in" and f[1] == col), None)

    def execute(self, q):
        if q.table == "media_evidence":
            if not self.evidence_table:
                raise _Missing('relation "media_evidence" does not exist')
            if q.op == "upsert":
                self.upserts.append(q.payload)
                return _R([])
            eqs = {f[1]: f[2] for f in q.filters if f[0] == "eq"}
            ids = self._ids(q, "media_id") or []
            return _R([r for r in self.evidence_rows if r["media_id"] in ids and all(r.get(k) == v for k, v in eqs.items())])
        if q.table == "media":
            ids = self._ids(q, "id") or []
            return _R([m for m in self.media if m["id"] in ids])
        if q.table == "observations":
            if q.op == "select":
                ids = self._ids(q, "media_id") or []
                return _R([o for o in self.observations if o["media_id"] in ids and o.get("deleted_at") is None])
            if q.op == "delete":
                self.deletes.append(q.filters)
                ids = self._ids(q, "media_id") or []
                eqs = {f[1]: f[2] for f in q.filters if f[0] == "eq"}
                self.observations = [o for o in self.observations if not (o["media_id"] in ids and all(o.get(k) == v for k, v in eqs.items()))]
                return _R([])
            if q.op == "insert":
                self.inserted.extend(q.payload)
                self.observations.extend(q.payload)
                return _R(q.payload)
        raise AssertionError(f"unexpected {q.table}.{q.op}")


def _jpeg(square=False) -> bytes:
    img = Image.new("RGB", (320, 240), (40, 40, 40))
    if square:
        ImageDraw.Draw(img).rectangle((100, 80, 220, 170), fill=(230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _media(mid, ts, path=None, exif=None):
    return {
        "id": mid,
        "deployment_id": DEP,
        "file_path": path or f"gdrive://MEDIA/A/{mid}.jpg",
        "file_name": f"{mid}.jpg",
        "timestamp": ts,
        "exif_metadata": exif,
    }


def _obs(mid, kind, version, conf=None, comment=None, source="ai", origin="cloud", review="ai_reviewed"):
    return {
        "id": f"obs-{mid}-{version}-{kind}-{review}",
        "media_id": mid,
        "deployment_id": DEP,
        "observation_type": kind,
        "source_type": source,
        "ai_origin": origin,
        "source_model_version": version,
        "classified_by": version,
        "confidence": conf,
        "classification_probability": None,
        "classification_timestamp": "t",
        "observation_comments": comment,
        "review_status": review,
        "deleted_at": None,
    }


def _evidence(mid, signal, value, source="speciesnet"):
    return {"media_id": mid, "signal": signal, "value": value, "source": source, "computed_at": "2026-09-29T00:00:00+00:00"}


def _enable(monkeypatch, fake):
    monkeypatch.setattr(settings, "FF_EVIDENCE_FUSION_ENABLED", True)
    monkeypatch.setattr(settings, "EVIDENCE_FUSION_THRESHOLD", 0.5)
    monkeypatch.setattr(settings, "BURST_GAP_SECONDS", 10.0)
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: fake)
    me.reset_probe()


# ── Pure builders ────────────────────────────────────────────────────

_SIGNALS = {
    "speciesnet_presence": 0.0,
    "speciesnet_type": "blank",
    "gemini_presence": 1.0,
    "neighbour_animal": 1.0,
    "motion": 0.6,
    "motion_frac": 0.012,
    "edge_presence": None,
    "edge_score": None,
    "near_threshold": 0.7,
    "burst_id": f"{DEP}:f1",
    "burst_index": 1,
    "burst_len": 6,
    "burst_animal_count": 2,
}


def test_consensus_row_columns_match_section_7_and_the_schema():
    row = build_consensus_observation({"id": "m1"}, DEP, 0.91, 0.5, _SIGNALS, "2026-09-29T00:00:00+00:00")
    assert row == {
        "id": row["id"],
        "deployment_id": DEP,
        "media_id": "m1",
        "observation_level": "media",
        "observation_type": "animal",
        "source_type": "consensus",
        "source_model_version": "evidence_fusion_v1",
        "review_status": "ai_reviewed",
        "confidence": 0.91,
        "classification_method": "machine",
        "classified_by": "evidence_fusion_v1",
        "classification_timestamp": "2026-09-29T00:00:00+00:00",
        "observation_comments": "evidence_fusion_v1 score=0.91 threshold=0.50 speciesnet=0 gemini=1 neighbour=1 motion=0.60 edge=absent near=0.70",
    }
    # NULL columns are left out of the insert: ai_origin (CHECK allows NULL), bbox (no box fusion in v1),
    # classifier_category, taxon, count, source_model_id.
    assert not any(k in row for k in ("ai_origin", "bbox_x", "classifier_category", "taxon_id", "count", "source_model_id"))
    assert EVIDENCE_FUSION_VERSION == "evidence_fusion_v1"


def test_consensus_type_follows_speciesnet_kept_type_and_clamps_confidence():
    human = build_consensus_observation({"id": "m1"}, DEP, 0.2, 0.5, {**_SIGNALS, "speciesnet_type": "human"}, "t")
    assert human["observation_type"] == "human"
    blank = build_consensus_observation({"id": "m1"}, DEP, 0.2, 0.5, _SIGNALS, "t")
    assert blank["observation_type"] == "blank" and blank["review_status"] == "ai_reviewed"
    assert build_consensus_observation({"id": "m1"}, DEP, 1.7, 0.5, _SIGNALS, "t")["confidence"] == 1.0


def test_fusion_evidence_signals_grouped_by_source():
    groups = fusion_evidence_signals({**_SIGNALS, "edge_presence": 1.0, "edge_score": 0.87, "edge_label": "rat"}, 0.91, 0.5)
    assert set(groups) == {"bursts", "motion", "edge", "fusion"}
    assert groups["bursts"] == {"burst_id": f"{DEP}:f1", "burst_index": 1, "burst_len": 6, "burst_animal_count": 2, "neighbour_animal": 1.0}
    assert groups["motion"] == {"motion_frac": 0.012}
    assert groups["edge"] == {"edge_presence": 1.0, "edge_score": (0.87, "rat")}
    assert groups["fusion"] == {"evidence_score": 0.91, "evidence_threshold": 0.5, "evidence_weights_version": "v1"}
    assert fusion_evidence_signals(_SIGNALS, 0.2, 0.5)["edge"] == {"edge_presence": None, "edge_score": None}


# ── Step run ─────────────────────────────────────────────────────────


async def test_step_writes_one_consensus_row_per_media_and_recovers_a_burst_neighbour(monkeypatch):
    media = [_media("a", "2026-06-16T17:42:00+00:00"), _media("b", "2026-06-16T17:42:02+00:00"), _media("c", "2026-06-16T18:00:00+00:00")]
    obs = [
        _obs("a", "blank", SN),
        _obs("a", "animal", GEM, comment="visibility=obscured; size=tiny; conditions=night_ir | dark shape"),
        _obs("b", "animal", SN, conf=0.8),
        _obs("b", "animal", GEM, comment="visibility=clear; size=small | rat"),
        _obs("c", "blank", SN),
        _obs("c", "blank", GEM),
    ]
    evidence = [_evidence("a", "speciesnet_max_conf", 0.14), _evidence("b", "speciesnet_max_conf", 0.8), _evidence("c", "speciesnet_max_conf", 0.03)]
    fake = _Fake(media, obs, evidence_table=True, evidence_rows=evidence)
    _enable(monkeypatch, fake)

    async def fake_resolve(path, size="full"):
        return (_jpeg(square=path.endswith("b.jpg")), "image/jpeg")

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)

    result = await EvidenceFusionStep().run(media, DEP, {"confidence_threshold": 0.2})
    rows = {r["media_id"]: r for r in fake.inserted}
    assert set(rows) == {"a", "b", "c"}
    assert all(r["source_type"] == "consensus" and r["source_model_version"] == EVIDENCE_FUSION_VERSION for r in rows.values())
    assert rows["b"]["observation_type"] == "animal" and rows["b"]["confidence"] == 1.0
    # a: SpeciesNet blank (0.14 box, near 0.70), Gemini obscured (0.8), neighbour b, the pixels moved:
    # 0.5 x 0.8 + 0.25 + 0.15 x 1.0 + 0.10 x 0.7 = 0.87
    assert rows["a"]["observation_type"] == "animal" and rows["a"]["confidence"] == 0.87
    assert (
        rows["a"]["observation_comments"]
        == "evidence_fusion_v1 score=0.87 threshold=0.50 speciesnet=0 gemini=0.80 neighbour=1 motion=1 edge=absent near=0.70"
        " det=0.20 frame_area=0.90 frame_conf=0.50 vehicle=dropped"
    )
    # c: singleton, both blank, near 0.15: score 0.015, confirmed blank, neighbour absent.
    assert rows["c"]["observation_type"] == "blank" and rows["c"]["confidence"] == 0.015
    assert "neighbour=absent" in rows["c"]["observation_comments"] and "motion=absent" in rows["c"]["observation_comments"]
    assert result.step == PipelineStepType.EVIDENCE_FUSION
    assert result.observations_created == 3 and result.media_processed == 3 and result.errors == 0
    assert result.counts == {
        "bursts": 2,
        "consensus_animal": 2,
        "consensus_blank": 1,
        "suspicious": 0,
        "camera_shift": 0,
        "evidence_rows": len(fake.upserts[0][0]),
    }
    assert result.model_version == EVIDENCE_FUSION_VERSION
    (evidence_rows, on_conflict) = fake.upserts[0]
    assert on_conflict == "media_id,signal,source,source_version"
    by = {(r["media_id"], r["signal"]): r for r in evidence_rows}
    assert by[("a", "burst_id")]["value_text"] == f"{DEP}:a" and by[("a", "burst_id")]["source"] == "bursts"
    assert by[("a", "burst_len")]["value"] == 2.0 and by[("c", "burst_len")]["value"] == 1.0
    assert by[("a", "neighbour_animal")]["value"] == 1.0 and ("c", "neighbour_animal") not in by  # absent: no row
    assert ("c", "motion_frac") not in by and by[("a", "motion_frac")]["source"] == "motion"
    assert by[("a", "evidence_score")]["value"] == 0.87 and by[("a", "evidence_score")]["source"] == "fusion"
    assert by[("a", "evidence_weights_version")]["value_text"] == "v1"
    assert by[("a", "evidence_threshold")]["value"] == 0.5
    assert not any(sig in ("speciesnet_presence", "gemini_presence", "edge_presence") for _, sig in by)  # other writers' signals, absent edge
    assert all(r["run_id"] for r in evidence_rows)


async def test_step_replaces_only_its_own_ai_reviewed_rows(monkeypatch):
    media = [_media("a", "2026-06-16T17:42:00+00:00")]
    stale = _obs("a", "animal", EVIDENCE_FUSION_VERSION, conf=0.9, source="consensus", origin=None)
    promoted = _obs("a", "animal", EVIDENCE_FUSION_VERSION, conf=0.9, source="consensus", origin=None, review="human_reviewed")
    other = _obs("a", "animal", "vote_v0", conf=0.9, source="consensus", origin=None)
    human = _obs("a", "animal", "human", source="human", origin=None)
    fake = _Fake(media, [stale, promoted, other, human, _obs("a", "blank", SN), _obs("a", "blank", GEM)], evidence_table=False)
    _enable(monkeypatch, fake)

    async def fake_resolve(path, size="full"):
        raise AssertionError("a singleton needs no image")

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", fake_resolve)
    result = await EvidenceFusionStep().run(media, DEP, {})
    assert result.observations_created == 1
    kept = {o["id"] for o in fake.observations}
    assert stale["id"] not in kept and {promoted["id"], other["id"], human["id"]} <= kept
    mine = [o for o in fake.observations if o["source_model_version"] == EVIDENCE_FUSION_VERSION and o["review_status"] == "ai_reviewed"]
    assert len(mine) == 1 and mine[0]["observation_type"] == "blank"
    delete_filters = fake.deletes[-1]
    assert ("eq", "source_model_version", EVIDENCE_FUSION_VERSION) in delete_filters and ("eq", "review_status", "ai_reviewed") in delete_filters
    await EvidenceFusionStep().run(media, DEP, {})
    assert len([o for o in fake.observations if o["source_model_version"] == EVIDENCE_FUSION_VERSION and o["review_status"] == "ai_reviewed"]) == 1


async def test_step_survives_a_missing_media_evidence_table(monkeypatch):
    media = [_media("a", "2026-06-16T17:42:00+00:00")]
    fake = _Fake(media, [_obs("a", "animal", GEM, comment="visibility=clear | rat")], evidence_table=False)
    _enable(monkeypatch, fake)
    result = await EvidenceFusionStep().run(media, DEP, {})
    assert result.observations_created == 1 and result.errors == 0
    assert fake.upserts == [] and result.counts["evidence_rows"] == 0
    assert fake.inserted[0]["observation_type"] == "animal" and fake.inserted[0]["confidence"] == 0.5
    assert "near=absent" in fake.inserted[0]["observation_comments"]  # no max_conf without the table
    assert me.table_available(fake) is False  # cached; the second run does not probe again


async def test_step_noops_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "FF_EVIDENCE_FUSION_ENABLED", False)
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: (_ for _ in ()).throw(AssertionError("must not touch the DB")))
    result = await EvidenceFusionStep().run([_media("a", "t")], DEP, {})
    assert result.media_processed == 0 and result.observations_created == 0


async def test_step_config_overrides_threshold_and_speciesnet_type_wins(monkeypatch):
    media = [_media("a", "2026-06-16T17:42:00+00:00"), _media("h", "2026-06-16T18:42:00+00:00")]
    fake = _Fake(
        media,
        [_obs("a", "animal", GEM, comment="visibility=obscured | rat"), _obs("h", "human", SN, conf=0.9), _obs("h", "blank", GEM)],
        evidence_table=False,
    )
    _enable(monkeypatch, fake)
    result = await EvidenceFusionStep().run(media, DEP, {"evidence_threshold": 0.9})
    rows = {r["media_id"]: r for r in fake.inserted}
    assert rows["a"]["observation_type"] == "blank" and rows["a"]["confidence"] == 0.4
    assert rows["h"]["observation_type"] == "human" and rows["h"]["confidence"] == 0.5  # SpeciesNet's kept type, below the raised threshold
    assert result.counts["consensus_animal"] == 0 and result.counts["consensus_blank"] == 2
    assert result.counts["suspicious"] == 2  # both sit in [0.25, 0.90): a is a real suspicious blank, h only because the threshold was raised


# ── Wiring ───────────────────────────────────────────────────────────


def test_build_pipeline_steps_places_fusion_last(monkeypatch):
    monkeypatch.setattr(settings, "FF_ML_ENABLED", True)
    monkeypatch.setattr(settings, "FF_PIPELINE_ENABLED", True)
    monkeypatch.setattr(settings, "FF_MEDIA_REGISTRY_ENABLED", True)
    monkeypatch.setattr(settings, "FF_SPECIESNET_ENABLED", True)
    monkeypatch.setattr(settings, "FF_BIOCLIP_ENABLED", True)
    monkeypatch.setattr(settings, "FF_GEMINI_PRESENCE_ENABLED", True)
    monkeypatch.setattr(settings, "FF_EVIDENCE_FUSION_ENABLED", True)
    steps = build_pipeline_steps()
    assert steps[-1] == PipelineStepType.EVIDENCE_FUSION
    assert steps.index(PipelineStepType.BIOCLIP) < steps.index(PipelineStepType.EVIDENCE_FUSION)
    monkeypatch.setattr(settings, "FF_EVIDENCE_FUSION_ENABLED", False)
    assert PipelineStepType.EVIDENCE_FUSION not in build_pipeline_steps()
