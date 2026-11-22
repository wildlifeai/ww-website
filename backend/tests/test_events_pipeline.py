# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the domain/events.py temporal clustering logic.

The pure algorithmic functions, and ``cluster_deployment_events`` end to end on
``tests/fake_postgrest.FakeDB``.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import events as events_domain
from app.domain.events import (
    _build_event_row,
    _cluster_temporal,
    _group_observations_by_species,
    cluster_deployment_events,
)
from tests.fake_postgrest import FakeDB

# ── Test Data Helpers ────────────────────────────────────────────────


def _obs(
    media_timestamp: str,
    taxon_id: str | None = None,
    confidence: float = 0.5,
    media_id: str | None = None,
) -> dict:
    """Create a minimal observation dict for testing."""
    return {
        "id": str(uuid.uuid4()),
        "deployment_id": "dep-001",
        "media_id": media_id or str(uuid.uuid4()),
        "taxon_id": taxon_id,
        "scientific_name": None,
        "confidence": confidence,
        "created_at": media_timestamp,
        "media_timestamp": media_timestamp,
    }


# ── _group_observations_by_species ───────────────────────────────────


class TestGroupObservationsBySpecies:
    def test_groups_by_taxon_id(self):
        obs = [
            _obs("2025-01-01T10:00:00Z", taxon_id="kiwi"),
            _obs("2025-01-01T10:05:00Z", taxon_id="possum"),
            _obs("2025-01-01T10:10:00Z", taxon_id="kiwi"),
        ]
        groups = _group_observations_by_species(obs)
        assert len(groups) == 2
        assert len(groups["kiwi"]) == 2
        assert len(groups["possum"]) == 1

    def test_unclassified_grouped_under_none(self):
        obs = [
            _obs("2025-01-01T10:00:00Z", taxon_id=None),
            _obs("2025-01-01T10:05:00Z", taxon_id=None),
        ]
        groups = _group_observations_by_species(obs)
        assert len(groups) == 1
        assert None in groups
        assert len(groups[None]) == 2

    def test_empty_list(self):
        groups = _group_observations_by_species([])
        assert groups == {}


# ── _cluster_temporal ────────────────────────────────────────────────


class TestClusterTemporal:
    def test_single_cluster_within_gap(self):
        """All observations within 30-minute gap → 1 cluster."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [_obs((base + timedelta(minutes=i * 5)).isoformat()) for i in range(5)]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_two_clusters_with_gap(self):
        """Observations split by a 60-minute gap → 2 clusters."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [
            _obs((base + timedelta(minutes=0)).isoformat()),
            _obs((base + timedelta(minutes=5)).isoformat()),
            _obs((base + timedelta(minutes=65)).isoformat()),  # gap > 30 min
            _obs((base + timedelta(minutes=70)).isoformat()),
        ]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 2
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 2

    def test_each_observation_is_own_cluster(self):
        """Observations 2 hours apart with 30-min gap → each is own cluster."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [_obs((base + timedelta(hours=i * 2)).isoformat()) for i in range(3)]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 3

    def test_empty_observations(self):
        clusters = _cluster_temporal([], gap_minutes=30)
        assert clusters == []

    def test_single_observation(self):
        obs = [_obs("2025-01-01T10:00:00Z")]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_exact_boundary_gap(self):
        """Observations exactly at the gap boundary → same cluster (<=)."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [
            _obs(base.isoformat()),
            _obs((base + timedelta(minutes=30)).isoformat()),
        ]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        # 30 minutes = 1800 seconds, gap_seconds = 30 * 60 = 1800
        # delta (1800) > gap_seconds (1800) is False → same cluster
        assert len(clusters) == 1

    def test_one_second_over_boundary(self):
        """Observations 30m01s apart with 30-min gap → 2 clusters."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [
            _obs(base.isoformat()),
            _obs((base + timedelta(minutes=30, seconds=1)).isoformat()),
        ]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 2

    def test_unsorted_input_is_handled(self):
        """Observations provided out of order should still cluster correctly."""
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        obs = [
            _obs((base + timedelta(minutes=65)).isoformat()),
            _obs(base.isoformat()),
            _obs((base + timedelta(minutes=5)).isoformat()),
        ]
        clusters = _cluster_temporal(obs, gap_minutes=30)
        assert len(clusters) == 2
        # First cluster: 10:00, 10:05. Second cluster: 11:05.
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 1


# ── _build_event_row ─────────────────────────────────────────────────


class TestBuildEventRow:
    def test_basic_event(self):
        base = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
        cluster = [
            _obs(base.isoformat(), confidence=0.3),
            _obs((base + timedelta(minutes=5)).isoformat(), confidence=0.9),
            _obs((base + timedelta(minutes=10)).isoformat(), confidence=0.6),
        ]
        row = _build_event_row(cluster, "dep-001", "kiwi-id", "user-001")
        assert row["deployment_id"] == "dep-001"
        assert row["taxon_id"] == "kiwi-id"
        assert row["event_duration_seconds"] == 600  # 10 minutes
        assert row["media_count"] == 3
        assert row["review_status"] == "ai_reviewed"
        assert row["created_by"] == "user-001"
        # Primary media should be from the highest confidence observation
        best_media = max(cluster, key=lambda o: o["confidence"])
        assert row["primary_media_id"] == best_media["media_id"]

    def test_single_observation_event(self):
        obs = _obs("2025-01-01T10:00:00Z", confidence=0.8)
        row = _build_event_row([obs], "dep-001", None)
        assert row["event_duration_seconds"] == 0
        assert row["media_count"] == 1
        assert row["taxon_id"] is None

    def test_confidence_average(self):
        cluster = [
            _obs("2025-01-01T10:00:00Z", confidence=0.4),
            _obs("2025-01-01T10:01:00Z", confidence=0.6),
        ]
        row = _build_event_row(cluster, "dep-001", "sp1")
        assert row["confidence"] == pytest.approx(0.5, abs=0.01)

    def test_uuid_is_generated(self):
        cluster = [_obs("2025-01-01T10:00:00Z")]
        row = _build_event_row(cluster, "dep-001", None)
        # Should be a valid UUID string
        uuid.UUID(row["id"])


# ── cluster_deployment_events on FakeDB (#458) ───────────────────────

DEP = "dep-1"
OLD_EVENT = "old-event"


class _Deployment:
    """Media and observation rows for one deployment, and the events they should form."""

    def __init__(self):
        self.media: list[dict] = []
        self.observations: list[dict] = []
        self.expected: set[frozenset[str]] = set()

    def add(self, ts: datetime, taxon: str | None, deployment_id: str = DEP, **extra) -> str:
        n = len(self.observations) + 1
        media_id, obs_id = f"m-{n:05d}", f"o-{n:05d}"
        self.media.append({"id": media_id, "deployment_id": deployment_id, "timestamp": ts.isoformat()})
        row = {"id": obs_id, "deployment_id": deployment_id, "media_id": media_id, "taxon_id": taxon, "confidence": 0.5}
        self.observations.append({**row, "created_at": ts.isoformat(), "deleted_at": None, "observation_event_id": None, **extra})
        return obs_id

    def event(self, start: datetime, taxon: str | None, frames: int, every: timedelta) -> None:
        """One expected event: ``frames`` photos of ``taxon``, ``every`` apart."""
        self.expected.add(frozenset(self.add(start + i * every, taxon) for i in range(frames)))

    def db(self) -> FakeDB:
        return FakeDB(
            {
                "media": self.media,
                "observations": self.observations,
                "observation_events": [
                    {"id": OLD_EVENT, "deployment_id": DEP},
                    {"id": "other-event", "deployment_id": "dep-2"},
                ],
                "annotation_runs": [],
            }
        )


def _linked(db: FakeDB) -> dict[str | None, set[str]]:
    """The deployment's live observations, grouped by the event they point at."""
    groups: dict[str | None, set[str]] = {}
    for o in db.rows("observations"):
        if o["deployment_id"] == DEP and o["deleted_at"] is None:
            groups.setdefault(o["observation_event_id"], set()).add(o["id"])
    return groups


async def test_cluster_events_reads_and_links_every_observation(monkeypatch):
    """1,100 observations, more than one PostgREST read returns, all end up in the right event.

    Before #458 only the first 1,000 were read, so 100 had no event and the events were built
    from part of the data.
    """
    dep = _Deployment()
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dep.event(day, "kiwi", 250, timedelta(minutes=1))  # one event, three link updates of 100 ids
    for burst in range(60):  # 60 bursts of 10 frames, two hours apart
        dep.event(day + timedelta(hours=2 * burst), "possum", 10, timedelta(seconds=10))
    for frame in range(250):  # blanks 45 minutes apart, each its own event
        dep.event(day + timedelta(minutes=45 * frame), None, 1, timedelta(0))
    # The state an earlier run left: three observations on an old event, one of them since deleted,
    # and another deployment's event that must survive.
    for row in dep.observations[:2]:
        row["observation_event_id"] = OLD_EVENT
    dep.add(day, "kiwi", deleted_at=day.isoformat(), observation_event_id=OLD_EVENT)
    dep.add(day, "kiwi", deployment_id="dep-2", observation_event_id="other-event")
    db = dep.db()
    monkeypatch.setattr(events_domain, "create_service_client", db.client)

    result = await cluster_deployment_events(DEP, gap_minutes=30)

    assert result.observations_linked == 1100
    assert result.events_created == len(dep.expected) == 311
    groups = _linked(db)
    assert None not in groups
    assert {frozenset(ids) for ids in groups.values()} == dep.expected
    events = {e["id"]: e for e in db.rows("observation_events")}
    assert all(events[event_id]["media_count"] == len(ids) for event_id, ids in groups.items())
    # The old event is gone and nothing points at it, so the delete cannot trip the foreign key;
    # the other deployment keeps its event.
    assert OLD_EVENT not in events
    assert all(o["observation_event_id"] != OLD_EVENT for o in db.rows("observations"))
    assert events["other-event"]["deployment_id"] == "dep-2"
    assert next(o for o in db.rows("observations") if o["deployment_id"] == "dep-2")["observation_event_id"] == "other-event"
    # Every update names at most 100 observation ids, so its URL stays short.
    assert max(w.rows for w in db.writes if w.table == "observations" and w.mode == "update") <= 100


async def test_cluster_events_links_several_events_at_once(monkeypatch):
    """The link updates overlap, up to the concurrency limit, and the result is the same."""
    import threading
    import time

    from tests.fake_postgrest import Query

    dep = _Deployment()
    day = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for frame in range(40):
        dep.event(day + timedelta(hours=frame), "kiwi", 1, timedelta(0))
    db = dep.db()
    lock = threading.Lock()
    in_flight = peak = 0

    class SlowUpdate(Query):
        def execute(self):
            nonlocal in_flight, peak
            if self.mode != "update" or "id" not in {path for path, _, _ in self.filters}:
                return super().execute()
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.01)
            try:
                return super().execute()
            finally:
                with lock:
                    in_flight -= 1

    monkeypatch.setattr(db, "table", lambda name: SlowUpdate(db, name))
    monkeypatch.setattr(events_domain, "create_service_client", db.client)

    result = await cluster_deployment_events(DEP, gap_minutes=30)

    assert result.events_created == 40
    assert {frozenset(ids) for ids in _linked(db).values()} == dep.expected
    assert 1 < peak <= events_domain._LINK_CONCURRENCY


# ── Schema validation tests ──────────────────────────────────────────


class TestPipelineSchemas:
    def test_pipeline_run_request_defaults(self):
        from app.schemas.pipeline import PipelineRunRequest, PipelineStepType

        req = PipelineRunRequest(deployment_id="abc-123")
        assert req.steps == [PipelineStepType.SPECIESNET]
        assert req.confidence_threshold == 0.2
        assert req.config == {}

    def test_cluster_events_request_defaults(self):
        from app.schemas.pipeline import ClusterEventsRequest

        req = ClusterEventsRequest(deployment_id="abc-123")
        assert req.gap_minutes == 30
        assert req.min_images == 1

    def test_cluster_events_request_validation(self):
        from app.schemas.pipeline import ClusterEventsRequest

        with pytest.raises(Exception):
            ClusterEventsRequest(deployment_id="x", gap_minutes=0)  # min 1
        with pytest.raises(Exception):
            ClusterEventsRequest(deployment_id="x", gap_minutes=1441)  # max 1440

    def test_pipeline_step_type_enum(self):
        from app.schemas.pipeline import PipelineStepType

        assert PipelineStepType.MEDIA_PREP.value == "media_prep"
        assert PipelineStepType.SPECIESNET.value == "speciesnet"
        assert PipelineStepType.ANIMAL_CROP.value == "animal_crop"


# ── Pipeline step registry tests ─────────────────────────────────────


class TestPipelineStepRegistry:
    def test_get_known_steps(self):
        from app.domain.pipeline import get_step
        from app.schemas.pipeline import PipelineStepType

        step = get_step(PipelineStepType.MEDIA_PREP)
        assert step.step_type == PipelineStepType.MEDIA_PREP

        step = get_step(PipelineStepType.SPECIESNET)
        assert step.step_type == PipelineStepType.SPECIESNET

        step = get_step(PipelineStepType.ANIMAL_CROP)
        assert step.step_type == PipelineStepType.ANIMAL_CROP

    def test_unknown_step_raises(self):
        from app.domain.pipeline import get_step
        from app.schemas.pipeline import PipelineStepType

        with pytest.raises(ValueError, match="Unknown pipeline step"):
            get_step(PipelineStepType.CUSTOM)
