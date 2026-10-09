# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""One AI run per deployment, and no AI row beside a human verdict (#284).

Covers the upload's AI job coalescing (one job, plus at most one follow-up), the
per-deployment run lock (in-process and through Redis), the human verdict check at
fetch time and again at write time (a run that starts before a review and writes
after it), and ``_fetch_media`` paging past PostgREST's 1,000-row cap.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from app.domain import pipeline
from app.domain.pipeline import PipelineBusyError, deployment_lock_name, run_pipeline
from app.jobs import definitions
from app.schemas.job import JobStatus
from app.schemas.pipeline import PipelineStepResult, PipelineStepType
from app.services import locks
from app.services.speciesnet_service import SPECIESNET_VERSION, Detection, ImagePrediction

DEP = "d0000000-0000-4000-8000-000000000284"
DEP_B = "d0000000-0000-4000-8000-000000000285"


# ── A small in-memory PostgREST ──────────────────────────────────────


class _R:
    def __init__(self, data):
        self.data = data


class _Query:
    """Filters, ordering, paging and writes over the fake's tables, with the 1,000-row cap."""

    CAP = 1000

    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.preds, self.orders, self.window = "select", None, [], [], None
        self._negate = False

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows if isinstance(rows, list) else [rows]
        return self

    upsert = insert

    def delete(self):
        self.op = "delete"
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def _add(self, pred):
        negate, self._negate = self._negate, False
        self.preds.append((lambda r: not pred(r)) if negate else pred)
        return self

    def eq(self, col, val):
        return self._add(lambda r: r.get(col) == val)

    def in_(self, col, vals):
        vals = list(vals)
        return self._add(lambda r: r.get(col) in vals)

    def is_(self, col, val):
        assert val == "null"
        return self._add(lambda r: r.get(col) is None)

    def like(self, col, pattern):
        return self._add(lambda r: str(r.get(col) or "").startswith(pattern.rstrip("%")))

    def gte(self, col, val):
        return self._add(lambda r: r.get(col) is not None and r.get(col) >= val)

    def order(self, col, desc=False):
        self.orders.append((col, desc))
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def limit(self, n):
        self.window = (0, n - 1)
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            rows.extend(dict(r) for r in self.payload)
            return _R(self.payload)
        hit = [r for r in rows if all(p(r) for p in self.preds)]
        if self.op == "delete":
            self.db.tables[self.name] = [r for r in rows if not any(r is h for h in hit)]
            return _R(hit)
        for col, desc in reversed(self.orders):
            hit.sort(key=lambda r: (r.get(col) is None, r.get(col) or ""), reverse=desc)
        start, end = self.window or (0, self.CAP - 1)
        return _R(hit[start : min(end, start + self.CAP - 1) + 1])


class _Db:
    def __init__(self, media=(), observations=()):
        self.tables = {"media": [dict(m) for m in media], "observations": [dict(o) for o in observations]}
        self.storage = MagicMock()

    def table(self, name):
        return _Query(self, name)

    def obs(self, media_id):
        return [o for o in self.tables["observations"] if o["media_id"] == media_id]


def _media(i, ts="2026-10-08T03:12:40"):
    return {
        "id": f"m{i:04d}",
        "deployment_id": DEP,
        "file_path": f"gdrive://{i}",
        "file_name": f"{i}.jpg",
        "file_mediatype": "image/jpeg",
        "timestamp": ts,
    }


def _obs(media_id, *, review="ai_reviewed", source="ai", version=SPECIESNET_VERSION, kind="vehicle", deleted=None):
    return {
        "id": f"o-{media_id}-{source}-{review}-{version}",
        "deployment_id": DEP,
        "media_id": media_id,
        "observation_type": kind,
        "source_type": source,
        "ai_origin": "cloud" if source == "ai" else None,
        "source_model_version": version,
        "review_status": review,
        "deleted_at": deleted,
    }


class _RecordingStep:
    """Stands in for a model step: records the media it got, optionally waits and writes AI rows."""

    def __init__(self, step_type, db=None, calls=None, delay=0.0, active=None):
        self.step_type, self.db, self.calls, self.delay, self.active = step_type, db, calls, delay, active

    async def run(self, media, deployment_id, config):
        if self.calls is not None:
            self.calls.append((deployment_id, [m["id"] for m in media]))
        if self.active is not None:
            self.active["now"][deployment_id] = self.active["now"].get(deployment_id, 0) + 1
            self.active["max"][deployment_id] = max(self.active["max"].get(deployment_id, 0), self.active["now"][deployment_id])
            self.active["total_max"] = max(self.active.get("total_max", 0), sum(self.active["now"].values()))
        await asyncio.sleep(self.delay)
        if self.db is not None:
            self.db.tables["observations"].extend(_obs(m["id"]) | {"deployment_id": deployment_id} for m in media)
        if self.active is not None:
            self.active["now"][deployment_id] -= 1
        return PipelineStepResult(step=self.step_type, media_processed=len(media))


# ── Part 3: paging ───────────────────────────────────────────────────


async def test_fetch_media_pages_past_1000_rows(monkeypatch):
    # 2,500 photos, 100 to a timestamp (so the id tiebreak matters), the first 1,200 annotated.
    media = [_media(i, ts=f"2026-10-08T03:{i // 100:02d}:00") for i in range(2500)]
    db = _Db(media, [_obs(f"m{i:04d}") for i in range(1200)])
    calls: list = []
    monkeypatch.setattr(pipeline, "create_service_client", lambda: db)
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t, calls=calls))

    result = await run_pipeline(DEP, [PipelineStepType.SPECIESNET])

    assert result.total_media == 1300
    assert calls == [(DEP, [f"m{i:04d}" for i in range(1200, 2500)])]


# ── Part 2: human verdicts ───────────────────────────────────────────


async def test_run_skips_photos_with_a_live_human_verdict(monkeypatch):
    media = [_media(i) for i in range(4)]
    verdicts = [
        _obs("m0001", review="human_reviewed", source="human", version=None, kind="blank"),
        _obs("m0002", review="expert_reviewed", source="human", version=None),
        _obs("m0003", review="human_reviewed", source="human", version=None, deleted="2026-10-08T04:00:00"),
    ]
    db = _Db(media, verdicts)
    calls: list = []
    monkeypatch.setattr(pipeline, "create_service_client", lambda: db)
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t, calls=calls))

    await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP])

    # A deleted verdict is no verdict, so m0003 is still analysed.
    assert calls == [(DEP, ["m0000", "m0003"])]


def _speciesnet(monkeypatch, db, during_predict=None):
    """Wire the real SpeciesNetStep to the fake database, a fake resolver and a fake model."""

    async def resolve(path, size="full"):
        return b"jpeg", "image/jpeg"

    class _Model:
        async def predict(self, paths):
            if during_predict:
                during_predict()
            box = Detection(category="1", observation_type="animal", confidence=0.9, bbox=(0.1, 0.1, 0.2, 0.2))
            return [ImagePrediction(filepath=p, detections=[box], scientific_name=None, common_name=None, classification_score=None) for p in paths]

    monkeypatch.setattr(pipeline, "create_service_client", lambda: db)
    monkeypatch.setattr("app.domain.media_resolver.resolve_media", resolve)
    monkeypatch.setattr("app.services.speciesnet_service.get_speciesnet_service", lambda: _Model())
    monkeypatch.setattr("app.services.media_evidence.write_signals", lambda *a, **k: 0)


async def test_review_after_the_run_started_gets_no_ai_row(monkeypatch):
    """The dev incident: a run picks two photos, a reviewer marks one blank while the model works."""
    db = _Db([_media(1), _media(2)])
    reviewed = _obs("m0001", review="human_reviewed", kind="blank")  # the reviewer's edit of SpeciesNet's row

    _speciesnet(monkeypatch, db, during_predict=lambda: db.tables["observations"].append(dict(reviewed)))
    with capture_logs() as logs:
        result = await run_pipeline(DEP, [PipelineStepType.SPECIESNET])

    assert result.total_media == 2  # both were unreviewed when the run started
    assert db.obs("m0001") == [reviewed]  # only the verdict, no machine row beside it
    assert [(o["review_status"], o["observation_type"]) for o in db.obs("m0002")] == [("ai_reviewed", "animal")]
    assert any(e["event"] == "pipeline_rows_held_for_human_verdict" and e["media"] == 1 for e in logs)


async def test_force_run_keeps_the_verdict_and_adds_no_row_of_the_same_model(monkeypatch):
    sn_verdict = _obs("m0001", review="human_reviewed", kind="blank")  # a reviewer corrected SpeciesNet
    manual = _obs("m0002", review="human_reviewed", source="human", version=None, kind="animal")
    stale = _obs("m0003")
    db = _Db([_media(1), _media(2), _media(3)], [sn_verdict, manual, stale])
    _speciesnet(monkeypatch, db)

    await run_pipeline(DEP, [PipelineStepType.SPECIESNET], force=True)

    assert db.obs("m0001") == [sn_verdict]
    # Another author's verdict does not stop a forced SpeciesNet row, which is kept beside it.
    assert sorted(o["source_type"] for o in db.obs("m0002")) == ["ai", "human"]
    m3 = db.obs("m0003")
    assert len(m3) == 1 and m3[0]["id"] != stale["id"]  # replaced, not appended


async def test_force_cannot_be_smuggled_in_through_the_step_config(monkeypatch):
    db = _Db([_media(1)])
    reviewed = _obs("m0001", review="human_reviewed", source="human", version=None)
    _speciesnet(monkeypatch, db, during_predict=lambda: db.tables["observations"].append(dict(reviewed)))

    await run_pipeline(DEP, [PipelineStepType.SPECIESNET], config={"force": True})

    assert db.obs("m0001") == [reviewed]


# ── Part 1: one run per deployment ───────────────────────────────────


async def test_two_runs_on_one_deployment_never_overlap(monkeypatch):
    media = [_media(i) for i in range(3)]
    db = _Db(media + [m | {"id": "b" + m["id"], "deployment_id": DEP_B} for m in media])
    active: dict = {"now": {}, "max": {}}
    monkeypatch.setattr(pipeline, "create_service_client", lambda: db)
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t, delay=0.05, active=active))

    runs = [run_pipeline(DEP, [PipelineStepType.MEDIA_PREP]) for _ in range(3)] + [run_pipeline(DEP_B, [PipelineStepType.MEDIA_PREP])]
    await asyncio.gather(*runs)

    assert active["max"] == {DEP: 1, DEP_B: 1}
    assert active["total_max"] == 2  # the lock is per deployment, not global
    assert locks._local == {}  # nothing left behind


async def test_a_waiting_run_reads_the_media_after_the_first_one_wrote(monkeypatch):
    """Cause 2 of #284: runs that started together all picked the same photos."""
    db = _Db([_media(i) for i in range(5)])
    calls: list = []
    started: list = []
    monkeypatch.setattr(pipeline, "create_service_client", lambda: db)
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t, db=db, calls=calls, delay=0.05))

    async def on_start(n):
        started.append((n, len(db.tables["observations"])))

    first, second = await asyncio.gather(
        run_pipeline(DEP, [PipelineStepType.SPECIESNET], on_start=lambda: on_start(1)),
        run_pipeline(DEP, [PipelineStepType.SPECIESNET], on_start=lambda: on_start(2)),
    )

    assert first.total_media == 5 and second.total_media == 0
    assert len(calls) == 1
    assert started == [(1, 0), (2, 5)]  # the second run only started once the first had written


async def test_a_busy_deployment_refuses_a_caller_that_will_not_wait(monkeypatch):
    monkeypatch.setattr(pipeline, "create_service_client", lambda: _Db([_media(1)]))
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t))

    async with locks.exclusive(deployment_lock_name(DEP)):
        with pytest.raises(PipelineBusyError):
            await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP], wait=False)
    assert (await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP], wait=False)).total_media == 1


class _FakeRedis:
    """Just enough of redis.asyncio for ``locks``: SET NX semantics on a dict shared by "processes"."""

    def __init__(self, held, fail=False):
        self.held, self.fail, self.closed, self.renewed = held, fail, 0, 0

    def lock(self, name, timeout, sleep, blocking, blocking_timeout):
        client = self

        class _Lock:
            async def acquire(self):
                if client.fail:
                    raise ConnectionError("redis down")
                while name in client.held:
                    if not blocking:
                        return False
                    await asyncio.sleep(0.01)
                client.held[name] = self
                return True

            async def reacquire(self):
                client.renewed += 1

            async def release(self):
                client.held.pop(name)

        return _Lock()

    async def aclose(self):
        self.closed += 1


async def test_the_redis_lock_excludes_another_process(monkeypatch):
    held = {"ww:lock:" + deployment_lock_name(DEP): "the ARQ worker"}
    clients: list = []
    monkeypatch.setattr(locks, "_redis_client", lambda: clients.append(_FakeRedis(held)) or clients[-1])
    monkeypatch.setattr(pipeline, "create_service_client", lambda: _Db([_media(1)]))
    calls: list = []
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t, calls=calls))

    # This process holds nothing, so only Redis can say no.
    with pytest.raises(PipelineBusyError):
        await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP], wait=False)

    waiting = asyncio.create_task(run_pipeline(DEP, [PipelineStepType.MEDIA_PREP]))
    await asyncio.sleep(0.05)
    assert calls == []  # still waiting for the other process
    held.clear()
    assert (await waiting).total_media == 1
    assert held == {} and all(c.closed for c in clients)


async def test_redis_down_falls_back_to_the_in_process_lock(monkeypatch):
    monkeypatch.setattr(locks, "_redis_client", lambda: _FakeRedis({}, fail=True))
    monkeypatch.setattr(pipeline, "create_service_client", lambda: _Db([_media(1)]))
    monkeypatch.setattr(pipeline, "get_step", lambda t: _RecordingStep(t))

    with capture_logs() as logs:
        result = await run_pipeline(DEP, [PipelineStepType.MEDIA_PREP])

    assert result.total_media == 1
    assert any(e["event"] == "redis_lock_unavailable" for e in logs)


async def test_the_redis_lock_is_renewed_while_held(monkeypatch):
    client = _FakeRedis({})
    monkeypatch.setattr(locks, "_redis_client", lambda: client)

    async with locks.exclusive("x", ttl_seconds=0.03):
        await asyncio.sleep(0.05)

    assert client.renewed >= 2 and client.held == {}


# ── Part 1: coalescing the upload's AI jobs ──────────────────────────


def test_plan_joins_a_queued_job_and_follows_a_processing_one():
    jobs = [
        {"job_id": "q1", "status": "queued", "deployment_ids": ["a"]},
        {"job_id": "p1", "status": "processing", "deployment_ids": ["a", "b"]},
    ]
    assert definitions.plan_ai_coalescing(["a", "b", "c"], jobs) == (["b", "c"], {"a": "q1"}, {"b": "p1"})
    assert definitions.plan_ai_coalescing(["a"], jobs) == ([], {"a": "q1"}, {})


async def test_a_300_photo_upload_makes_one_job_and_one_follow_up(monkeypatch):
    """30 chunks of 10 photos arrive together, then 10 more while the job runs."""
    api_jobs: dict[str, dict] = {}

    async def find_active():
        await asyncio.sleep(0)  # let the other chunks interleave, as the Supabase round trip does
        return [{"job_id": k, **v} for k, v in api_jobs.items() if v["status"] in ("queued", "processing")]

    async def create_job(user_id=None, kind=None, label=None, deployment_ids=None):
        await asyncio.sleep(0)
        job_id = f"job{len(api_jobs)}"
        api_jobs[job_id] = {"status": "queued", "deployment_ids": deployment_ids}
        return job_id

    async def flush(job_id=None):
        return None

    monkeypatch.setattr(definitions, "find_active_ai_jobs", find_active)
    monkeypatch.setattr(definitions, "create_job", create_job)
    monkeypatch.setattr(definitions, "flush_pending_syncs", flush)

    first = await asyncio.gather(*(definitions.reserve_ai_job([DEP], "u") for _ in range(30)))
    assert list(api_jobs) == ["job0"]
    assert sum(1 for r in first if r[0]) == 1 and all(r[2] == {DEP: "job0"} for r in first if not r[0])

    api_jobs["job0"]["status"] = "processing"  # it has read its media; new photos need a follow-up
    later = await asyncio.gather(*(definitions.reserve_ai_job([DEP], "u") for _ in range(10)))
    assert list(api_jobs) == ["job0", "job1"]
    made = [r for r in later if r[0]]
    assert made == [("job1", [DEP], {}, {DEP: "job0"})]


async def test_the_ai_job_turns_processing_only_once_it_holds_a_deployment(monkeypatch):
    events: list = []

    async def update_job(job_id, status=None, **_k):
        if status is not None:
            events.append(status.value)

    async def flush(job_id=None):
        events.append("flushed")

    async def run(**kwargs):
        events.append(f"run {kwargs['deployment_id'][-3:]}")
        await kwargs["on_start"]()

    async def noop(*_a, **_k):
        return None

    from app.domain import edge_reflection

    monkeypatch.setattr(definitions, "update_job", update_job)
    monkeypatch.setattr(definitions, "flush_pending_syncs", flush)
    monkeypatch.setattr(definitions, "build_pipeline_steps", lambda: [PipelineStepType.SPECIESNET])
    monkeypatch.setattr(edge_reflection, "reflect_edge_deployment", noop)
    monkeypatch.setattr(pipeline, "run_pipeline", run)
    monkeypatch.setattr(definitions, "emit_detection_notifications", noop)
    monkeypatch.setattr(definitions, "auto_embed_deployment", noop)

    await definitions.annotate_deployments_job("job", [DEP, DEP_B])

    assert events == ["run 284", JobStatus.PROCESSING.value, "flushed", "run 285", JobStatus.COMPLETED.value]


# ── /api/pipeline/run says busy instead of hanging ───────────────────


async def test_pipeline_route_reports_a_busy_deployment(monkeypatch):
    from types import SimpleNamespace

    from app.routers import pipeline as route
    from app.schemas.pipeline import PipelineRunRequest

    seen: dict = {}

    async def busy(**kwargs):
        seen.update(kwargs)
        raise PipelineBusyError(kwargs["deployment_id"])

    async def allowed(*_a, **_k):
        return None

    monkeypatch.setattr(route, "run_pipeline", busy)
    monkeypatch.setattr(route, "assert_access", allowed)
    # The undecorated handler: the router is flag-gated out of the test app and the limiter needs a real request.
    handler = route.run_inference_pipeline.__wrapped__
    response = await handler(SimpleNamespace(state=SimpleNamespace(request_id="r")), PipelineRunRequest(deployment_id=DEP), SimpleNamespace(id="u"))

    assert seen["wait"] is False
    assert response.error.code == "PIPELINE_BUSY" and response.error.retryable is True
