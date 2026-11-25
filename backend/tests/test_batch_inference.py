# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The inference split: results parts, the precomputed providers, the cluster job, ingest, and parity.

The parity tests run the real pipeline steps twice on the same fixture photos, once with the
models answering in-process and once from a results part the cluster code wrote, and require
identical observation rows. The models are fakes at the service boundary (deterministic in
the image bytes), so CI needs no weights.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.config import settings
from app.jobs import batch_infer, ingest
from app.services import bioclip_service, dinov3, inference_cache, locks, object_store, speciesnet_service
from app.services.bioclip_service import CropPrediction
from app.services.speciesnet_service import Detection, ImagePrediction

DEP = "d0000000-0000-4000-8000-0000000000aa"
JOB = "a0000000-0000-4000-8000-0000000000aa"
LEASE = "1ea5e000-0000-4000-8000-0000000000aa"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(locks, "_lease_client", lambda: None)
    monkeypatch.setattr(settings, "OBJECT_STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_OBJECT_STORE_DIR", str(tmp_path / "objects"))


def _photo(seed: int) -> bytes:
    img = Image.new("RGB", (64, 48), (seed * 40 % 256, seed * 90 % 256, 120))
    img.putpixel((seed % 64, seed % 48), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


PHOTOS = {f"m000000{n}-0000-4000-8000-000000000000": _photo(n) for n in range(1, 4)}


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeSpeciesNet:
    """Two animals per photo, boxes and scores from the bytes."""

    version = speciesnet_service.SPECIESNET_VERSION

    async def predict(self, image_paths):
        out = []
        for path in image_paths:
            h = _h(Path(path).read_bytes())
            a, b = int(h[:2], 16) / 2550, int(h[2:4], 16) / 2550
            dets = [
                Detection("1", "animal", 0.9, (0.1 + a, 0.1, 0.3, 0.4)),
                Detection("1", "animal", 0.55, (0.5, 0.4 + b, 0.2, 0.25)),
            ]
            out.append(ImagePrediction(path, dets, f"genus species{h[:4]}", f"animal {h[:4]}", 0.8, {"species": f"species{h[:4]}"}))
        return out


class FakeBioCLIP:
    version = bioclip_service.BIOCLIP_VERSION

    async def predict(self, image_paths, labels=None, rank="species", keys=None):
        out = []
        for path in image_paths:
            h = _h(Path(path).read_bytes())
            out.append(CropPrediction(path, f"bioclip {h[:6]}", f"common {h[:6]}", 0.5 + int(h[:2], 16) / 600, rank))
        return out


class FakeDino:
    def __init__(self, name=None):
        from app.registries.embedding_registry import get_model_spec

        self.model_name = name or settings.EMBEDDING_DEFAULT_MODEL
        self.dim = get_model_spec(self.model_name).embedding_dim
        self.version = "fake"

    async def embed(self, images, keys=None):
        return [[int(_h(img)[i % 64], 16) / 15 for i in range(self.dim)] for img in images]


FAKES = batch_infer.Models(speciesnet=FakeSpeciesNet, bioclip=FakeBioCLIP, dinov3=FakeDino)


# ── the results part ─────────────────────────────────────────────────


def test_a_part_round_trips_and_checks_its_contract_lease_and_job():
    records = [{"media_id": "m1", "deployment_id": DEP, "speciesnet": None, "bioclip": [{"bbox": [0.1, 0.2, 0.3, 0.4], "score": 0.7}]}]
    data = inference_cache.encode_part({"lease_id": LEASE, "job_id": JOB, "part": 1, "kind": "annotate"}, records)
    part = inference_cache.decode_part(data, lease_id=LEASE, job_id=JOB)
    assert part.records == records
    assert ("m1", (0.1, 0.2, 0.3, 0.4)) in part.bioclip
    with pytest.raises(inference_cache.ContractMismatch):
        inference_cache.decode_part(data, lease_id="another-lease")
    with pytest.raises(inference_cache.ContractMismatch):
        inference_cache.decode_part(data, job_id="another-job")
    old = gzip.compress(json.dumps({"contract": 0, "lease_id": "x", "job_id": "y"}).encode())
    with pytest.raises(inference_cache.ContractMismatch):
        inference_cache.decode_part(old)


def test_unreadable_photos_are_kept_out_of_the_run():
    part = inference_cache.ResultsPart(
        header={}, records=[{"media_id": "m1", "deployment_id": DEP}, {"media_id": "m2", "deployment_id": DEP, "error": "download: 404"}]
    )
    assert part.by_deployment() == {DEP: ["m1"]} and part.media_ids == ["m1"]


def test_bbox_keys_survive_a_float_round_trip():
    assert inference_cache.bbox_key([0.1 + 1e-12, 0.2, 0.3, 0.4]) == inference_cache.bbox_key((0.1, 0.2, 0.3, 0.4))


async def test_getters_answer_from_the_part_only_while_it_is_active():
    part = inference_cache.ResultsPart(header={"bioclip_rank": "species"}, records=[])
    assert not isinstance(speciesnet_service.get_speciesnet_service(), inference_cache.PrecomputedSpeciesNet)
    with inference_cache.use(part):
        assert isinstance(speciesnet_service.get_speciesnet_service(), inference_cache.PrecomputedSpeciesNet)
        assert isinstance(bioclip_service.get_bioclip_service(), inference_cache.PrecomputedBioCLIP)
        assert isinstance(dinov3.get_dinov3_service(), inference_cache.PrecomputedDinoV3)
    assert inference_cache.active() is None


async def test_precomputed_bioclip_refuses_other_labels_or_missing_keys():
    provider = inference_cache.PrecomputedBioCLIP(inference_cache.ResultsPart(header={"bioclip_rank": "species"}, records=[]))
    with pytest.raises(inference_cache.ContractMismatch):
        await provider.predict(["a.jpg"], labels=["cat"], keys=[("m", None)])
    with pytest.raises(inference_cache.ContractMismatch):
        await provider.predict(["a.jpg"])
    assert await provider.predict(["a.jpg"], keys=[("m", None)]) == []
    assert provider.part.misses["bioclip"] == 1


async def test_precomputed_dinov3_checks_ids_and_dimensions():
    dim = FakeDino().dim
    part = inference_cache.ResultsPart(header={}, records=[{"media_id": "m1", "dinov3": [0.0] * dim}, {"media_id": "m2", "dinov3": [0.0] * 3}])
    provider = inference_cache.PrecomputedDinoV3(part)
    assert await provider.embed([b""], keys=["m1"]) == [[0.0] * dim]
    for keys in (["m2"], ["m9"], None):
        with pytest.raises(inference_cache.ContractMismatch):
            await provider.embed([b""], keys=keys)


# ── the cluster job ──────────────────────────────────────────────────


class FakeApi:
    """The lease endpoints in memory, one lease of one annotate job; ``paged_items`` answers the items pages."""

    def __init__(self, manifest: dict, paged_items: list[dict] | None = None):
        self.manifest, self.parts, self.calls = manifest, {}, []
        self.served = False
        self.paged_items = paged_items or []

    async def post(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        if path == "/api/worker/lease":
            if self.served:
                return {"lease_id": None}
            self.served = True
            return self.manifest
        if path.endswith("/part"):
            return {"method": "PUT", "path": f"/parts/{body['job_id']}/{body['part']}", "headers": {}}
        return {}

    async def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        start = params["offset"]
        return {"items": self.paged_items[start : start + params["limit"]], "offset": start, "total": len(self.paged_items)}

    async def fetch(self, item: dict) -> bytes:
        if item["media_id"] not in PHOTOS:
            raise OSError("404")
        return PHOTOS[item["media_id"]]

    async def put(self, target: dict, data: bytes) -> None:
        self.parts[target["path"]] = data


def _manifest(items, kind="annotate", per_detection=True, models=None):
    config = {"confidence_threshold": 0.2, "per_detection": per_detection, "bioclip_rank": "species", "bioclip_labels": None}
    from app.domain.pipeline import DetectionCutoffs

    config.update(DetectionCutoffs.from_config(config).as_config())
    return {
        "lease_id": LEASE,
        "heartbeat_seconds": 3600,
        "part_max_photos": 2,
        "jobs": [{"job_id": JOB, "kind": kind, "models": models or {"speciesnet": True, "bioclip": True}, "config": config, "items": items}],
    }


async def test_the_cluster_job_uploads_parts_reports_failures_and_stops_when_the_queue_is_empty(tmp_path):
    items = [{"media_id": m, "deployment_id": DEP, "path": f"/media/{m}"} for m in PHOTOS] + [
        {"media_id": "gone", "deployment_id": DEP, "path": "/x"}
    ]
    api = FakeApi(_manifest(items))
    assert await batch_infer.run(api, tmp_path / "work", FAKES) == 1  # pyright: ignore[reportArgumentType]

    assert sorted(api.parts) == [f"/parts/{JOB}/1", f"/parts/{JOB}/2"]
    done = [b for p, b in api.calls if p.endswith("/done")][0]
    assert done == {"jobs": [{"job_id": JOB, "parts_total": 2, "failed_media": ["gone"]}]}
    completes = [b["part"] for p, b in api.calls if p.endswith("/part/complete")]
    assert completes == [1, 2]
    second = inference_cache.decode_part(api.parts[f"/parts/{JOB}/2"], lease_id=LEASE, job_id=JOB)
    assert [r.get("error", "").startswith("download") for r in second.records] == [False, True]
    first = inference_cache.decode_part(api.parts[f"/parts/{JOB}/1"])
    assert all(r["speciesnet"] and len(r["bioclip"]) == 2 for r in first.records)
    assert not list((tmp_path / "work").iterdir())  # the photos are cleaned up after each part


async def test_the_cluster_job_asks_for_pages_and_reads_each_part_just_before_it(tmp_path):
    """#457: the photos of a paged job come a part at a time, so their URLs are signed just before use."""
    items = [{"media_id": m, "deployment_id": DEP, "path": f"/media/{m}"} for m in PHOTOS] + [
        {"media_id": "gone", "deployment_id": DEP, "path": "/x"}
    ]
    manifest = _manifest([])
    job = manifest["jobs"][0]
    del job["items"]
    job["items_total"] = len(items)
    api = FakeApi(manifest, paged_items=items)
    assert await batch_infer.run(api, tmp_path / "work", FAKES) == 1  # pyright: ignore[reportArgumentType]

    assert api.calls[0] == ("/api/worker/lease", {"slurm_job_id": "", "paged": True})
    paths = [p for p, _ in api.calls]
    page_path = f"/api/worker/lease/{LEASE}/items/{JOB}"
    assert [b for p, b in api.calls if p == page_path] == [{"offset": 0, "limit": 2}, {"offset": 2, "limit": 2}]
    second_page = len(paths) - 1 - paths[::-1].index(page_path)
    assert paths.index(f"/api/worker/lease/{LEASE}/part/complete") < second_page
    done = [b for p, b in api.calls if p.endswith("/done")][0]
    assert done == {"jobs": [{"job_id": JOB, "parts_total": 2, "failed_media": ["gone"]}]}


async def test_fetch_follows_the_apis_redirect_without_the_token():
    """An original too large for the API's answer comes from a presigned copy; the token stays with the API."""
    import httpx

    seen: list[tuple] = []

    def api_side(request: httpx.Request) -> httpx.Response:
        seen.append(("api", request.url.path, request.url.params.get("offset"), request.headers.get("authorization")))
        if request.url.path.endswith("/items/j1"):
            return httpx.Response(200, json={"items": [], "offset": 4, "total": 4})
        return httpx.Response(307, headers={"location": "https://s3.example/buffer/media/abc?sig=1"})

    def plain_side(request: httpx.Request) -> httpx.Response:
        seen.append(("plain", str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, content=b"big-jpeg")

    authed = httpx.AsyncClient(base_url="https://api.example", headers={"Authorization": "Bearer t"}, transport=httpx.MockTransport(api_side))
    plain = httpx.AsyncClient(transport=httpx.MockTransport(plain_side))
    api = batch_infer.Api("https://api.example", "t", authed=authed, plain=plain)
    try:
        assert await api.fetch({"media_id": "m1", "path": "/api/worker/lease/L/media/m1"}) == b"big-jpeg"
        assert (await api.get("/api/worker/lease/L/items/j1", {"offset": 4, "limit": 2}))["total"] == 4
    finally:
        await api.aclose()
    assert seen == [
        ("api", "/api/worker/lease/L/media/m1", None, "Bearer t"),
        ("plain", "https://s3.example/buffer/media/abc?sig=1", None),
        ("api", "/api/worker/lease/L/items/j1", "4", "Bearer t"),
    ]


# ── parity: in-process models versus the cluster's part ──────────────


class FakeSvc:
    """A Supabase client that records inserts and answers the crop reads of the per-crop step."""

    def __init__(self, crop_rows=None):
        self.inserts: dict[str, list[dict]] = {}
        self.crop_rows = crop_rows or []

    def table(self, name):
        return _FakeQuery(self, name)


class _FakeQuery:
    def __init__(self, svc, table):
        self.svc, self.table, self.cols, self.op = svc, table, "", "select"

    def select(self, cols="*", **kw):
        self.cols = cols
        return self

    def insert(self, rows):
        self.op = "insert"
        rows = rows if isinstance(rows, list) else [rows]
        self.svc.inserts.setdefault(self.table, []).extend(rows)
        return self

    @property
    def not_(self):
        return self

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        from types import SimpleNamespace

        if self.op == "select" and self.table == "observations" and "crop_url" in self.cols:
            return SimpleNamespace(data=self.svc.crop_rows, count=0)
        return SimpleNamespace(data=[], count=0)


def _rows(svc: FakeSvc) -> list[dict]:
    """Observation rows minus what differs run to run (ids, times, run ids)."""
    out = []
    for r in svc.inserts.get("observations", []):
        out.append({k: v for k, v in r.items() if k != "id" and "timestamp" not in k and not k.endswith("_at") and k != "run_id"})
    return sorted(out, key=lambda r: json.dumps(r, sort_keys=True, default=str))


async def _cluster_part(tmp_path, per_detection: bool) -> inference_cache.ResultsPart:
    items = [{"media_id": m, "deployment_id": DEP, "path": f"/media/{m}"} for m in PHOTOS]
    manifest = _manifest(items, per_detection=per_detection)
    manifest["part_max_photos"] = 10
    api = FakeApi(manifest)
    await batch_infer.run(api, tmp_path / f"work-{per_detection}", FAKES)  # pyright: ignore[reportArgumentType]
    (data,) = api.parts.values()
    return inference_cache.decode_part(data)


@pytest.fixture
def photos_on_disk(monkeypatch):
    """resolve_media serves the fixture photos, and crops of them as generate_observation_crops stores them."""
    from app.domain.media_registry import CROP_PADDING
    from app.services import image_processing as imgproc

    async def resolve_media(path, size="full", media=None):
        if path.startswith("crop://"):
            mid, box = path[len("crop://") :].split("|")
            return imgproc.crop_bbox(PHOTOS[mid], tuple(json.loads(box)), CROP_PADDING), "image/jpeg"
        return PHOTOS[path], "image/jpeg"

    monkeypatch.setattr("app.domain.media_resolver.resolve_media", resolve_media)
    monkeypatch.setattr(speciesnet_service, "_service", FakeSpeciesNet())
    monkeypatch.setattr(bioclip_service, "_service", FakeBioCLIP())
    monkeypatch.setattr(settings, "FF_BIOCLIP_ENABLED", True)


def _media():
    return [{"id": m, "deployment_id": DEP, "file_path": m, "file_name": f"{m}.jpg"} for m in PHOTOS]


@pytest.mark.parametrize("per_detection", [True, False])
async def test_speciesnet_rows_are_identical_from_the_cluster(tmp_path, monkeypatch, photos_on_disk, per_detection):
    from app.domain.pipeline import SpeciesNetStep

    monkeypatch.setattr(settings, "FF_PER_CROP_CLASSIFY_ENABLED", per_detection)
    config = {"confidence_threshold": 0.2}
    real = FakeSvc()
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: real)
    await SpeciesNetStep().run(_media(), DEP, dict(config))

    part = await _cluster_part(tmp_path, per_detection)
    cached = FakeSvc()
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: cached)
    with inference_cache.use(part):
        await SpeciesNetStep().run(_media(), DEP, dict(config))

    assert _rows(real) and _rows(real) == _rows(cached)
    assert part.misses["speciesnet"] == 0


async def test_per_crop_bioclip_rows_are_identical_from_the_cluster(tmp_path, monkeypatch, photos_on_disk):
    from app.domain.pipeline import BioCLIPStep, SpeciesNetStep, bbox_of

    monkeypatch.setattr(settings, "FF_PER_CROP_CLASSIFY_ENABLED", True)
    # The SpeciesNet rows the per-crop step reads back, with the crops the crop step stored.
    seed = FakeSvc()
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: seed)
    await SpeciesNetStep().run(_media(), DEP, {"confidence_threshold": 0.2})
    crop_rows = [
        {**r, "crop_url": f"crop://{r['media_id']}|{json.dumps(list(bbox_of(r)))}"}
        for r in seed.inserts["observations"]
        if r.get("observation_type") == "animal" and bbox_of(r)
    ]
    assert len(crop_rows) == 2 * len(PHOTOS)

    real = FakeSvc(crop_rows)
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: real)
    await BioCLIPStep().run(_media(), DEP, {"confidence_threshold": 0.0})

    part = await _cluster_part(tmp_path, per_detection=True)
    monkeypatch.setattr(bioclip_service, "_service", None)  # the model must not be reachable now
    cached = FakeSvc(crop_rows)
    monkeypatch.setattr("app.domain.pipeline.create_service_client", lambda: cached)
    with inference_cache.use(part):
        await BioCLIPStep().run(_media(), DEP, {"confidence_threshold": 0.0})

    assert _rows(real) and _rows(real) == _rows(cached)
    assert part.misses["bioclip"] == 0


async def test_embed_vectors_are_identical_from_the_cluster(tmp_path):
    crops = {m: data for m, data in PHOTOS.items()}
    items = [{"media_id": m, "deployment_id": DEP, "url": f"https://crops/{m}"} for m in crops]
    manifest = _manifest(items, kind="embed", models={"dinov3": settings.EMBEDDING_DEFAULT_MODEL})
    manifest["part_max_photos"] = 10
    api = FakeApi(manifest)
    await batch_infer.run(api, tmp_path / "work", FAKES)  # pyright: ignore[reportArgumentType]
    (data,) = api.parts.values()
    part = inference_cache.decode_part(data)
    expected = await FakeDino().embed(list(crops.values()))
    with inference_cache.use(part):
        got = await dinov3.get_dinov3_service().embed([b""] * len(crops), keys=list(crops))
    assert got == expected


# ── ingest ───────────────────────────────────────────────────────────


async def _store_part(records, part=1, kind="annotate"):
    from app.domain.batch_lease import part_key

    data = inference_cache.encode_part({"lease_id": LEASE, "job_id": JOB, "part": part, "kind": kind}, records)
    await object_store.put_object(part_key(LEASE, JOB, part), data)


@pytest.fixture
def ingest_env(monkeypatch):
    from app.schemas.pipeline import PipelineStepType

    seen: list[tuple[str, list[str], Any]] = []

    async def run_pipeline(deployment_id, steps, user_id=None, force=False, media_ids=None, **kw):
        seen.append((deployment_id, list(media_ids or []), inference_cache.active()))

    monkeypatch.setattr("app.domain.pipeline.run_pipeline", run_pipeline)
    monkeypatch.setattr("app.domain.edge_reflection.reflect_edge_deployment", AsyncMock())
    monkeypatch.setattr("app.jobs.definitions.build_pipeline_steps", lambda: [PipelineStepType.SPECIESNET])
    monkeypatch.setattr(ingest, "refresh_job", AsyncMock(return_value=True))
    monkeypatch.setattr(ingest, "update_job", AsyncMock())
    dispatch = {"backend": "batch", "name": "annotate_deployments_job", "kind": "annotate", "args": [[DEP], "u1"], "kwargs": {}}
    monkeypatch.setattr(ingest, "_dispatch_of", AsyncMock(return_value=dispatch))
    monkeypatch.setattr(settings, "FF_WILDLIFE_BRAIN_ENABLED", True)
    return seen


async def test_ingest_runs_the_pipeline_on_the_part_once(ingest_env, monkeypatch):
    finalize = AsyncMock(return_value=False)
    monkeypatch.setattr(ingest, "finalize_if_complete", finalize)
    await _store_part([{"media_id": "m1", "deployment_id": DEP}, {"media_id": "m2", "deployment_id": DEP, "error": "download: 404"}])

    assert await ingest.ingest_results_part(LEASE, JOB, 1) == "ingested"
    ((dep, media_ids, active),) = ingest_env
    assert dep == DEP and media_ids == ["m1"] and active is not None
    assert await ingest.ingest_results_part(LEASE, JOB, 1) == "skipped"
    assert len(ingest_env) == 1 and finalize.await_count == 1


async def test_ingest_refuses_a_part_of_another_job(ingest_env):
    from app.domain.batch_lease import part_key

    data = inference_cache.encode_part({"lease_id": LEASE, "job_id": "someone-else", "part": 1, "kind": "annotate"}, [])
    await object_store.put_object(part_key(LEASE, JOB, 1), data)
    with pytest.raises(inference_cache.ContractMismatch):
        await ingest.ingest_results_part(LEASE, JOB, 1)


async def test_finalize_waits_for_every_part_then_closes_the_job_once(ingest_env, monkeypatch):
    from app.domain.batch_lease import done_key

    created = AsyncMock(return_value="e0000000-0000-4000-8000-000000000000")
    monkeypatch.setattr(ingest, "create_job", created)
    enqueue = AsyncMock()
    monkeypatch.setattr("app.jobs.dispatch.enqueue_job", enqueue)
    monkeypatch.setattr("app.services.notifications_service.emit_detection_notifications", AsyncMock())

    await object_store.put_object(done_key(LEASE, JOB), json.dumps({"parts_total": 2, "failed_media": ["m9"]}).encode())
    await _store_part([{"media_id": "m1", "deployment_id": DEP}], part=1)
    await _store_part([{"media_id": "m2", "deployment_id": DEP}], part=2)
    await ingest.ingest_results_part(LEASE, JOB, 1)
    assert not await ingest.finalize_if_complete(LEASE, JOB)  # part 2 is not ingested yet
    await ingest.ingest_results_part(LEASE, JOB, 2)  # this one finishes the job

    enqueue.assert_awaited_once_with("embed_deployments_job", created.return_value, [DEP])
    final = ingest.update_job.await_args_list[-1]  # pyright: ignore[reportAttributeAccessIssue]
    assert final.kwargs["status"].value == "completed" and "1 photo(s) could not be read" in final.kwargs["message"]
    assert not await ingest.finalize_if_complete(LEASE, JOB)  # already finalised


async def test_finalize_runs_an_embed_job_on_all_its_vectors(monkeypatch):
    from app.domain.batch_lease import done_key

    dispatch = {"backend": "batch", "name": "embed_deployment_job", "kind": "embed", "args": [DEP, None], "kwargs": {}}
    monkeypatch.setattr(ingest, "_dispatch_of", AsyncMock(return_value=dispatch))
    monkeypatch.setattr(ingest, "refresh_job", AsyncMock(return_value=True))
    seen = {}

    async def embed_deployment_job(job_id, deployment_id, model_name=None):
        part = inference_cache.active()
        seen.update(job=job_id, dep=deployment_id, vectors=sorted(part.dinov3) if part else None)

    monkeypatch.setattr("app.jobs.definitions.embed_deployment_job", embed_deployment_job)
    await _store_part([{"media_id": "m1", "deployment_id": DEP, "dinov3": [0.1]}], part=1, kind="embed")
    await _store_part([{"media_id": "m2", "deployment_id": DEP, "dinov3": [0.2]}], part=2, kind="embed")
    await object_store.put_object(done_key(LEASE, JOB), json.dumps({"parts_total": 2}).encode())
    await ingest.ingest_results_part(LEASE, JOB, 1)
    await ingest.ingest_results_part(LEASE, JOB, 2)
    assert seen == {"job": JOB, "dep": DEP, "vectors": ["m1", "m2"]}


async def test_wildlife_brain_embeds_only_crops_with_a_cluster_vector(monkeypatch):
    from app.domain import wildlife_brain

    crops = [{"id": "m1", "deployment_id": DEP, "crop_url": "u1"}, {"id": "m2", "deployment_id": DEP, "crop_url": "u2"}]
    monkeypatch.setattr(wildlife_brain, "_fetch_crops", AsyncMock(return_value=crops))
    monkeypatch.setattr(wildlife_brain, "_create_embedding_run", AsyncMock(return_value="run1"))
    monkeypatch.setattr(wildlife_brain, "_finish_embedding_run", AsyncMock())
    resolve = AsyncMock()
    monkeypatch.setattr(wildlife_brain, "_resolve_crops_concurrent", resolve)
    captured = {}

    class Store:
        async def upsert(self, ids, vectors, payloads):
            captured["ids"] = ids

    monkeypatch.setattr("app.services.vector_store.get_vector_service", lambda model: Store())
    monkeypatch.setattr(wildlife_brain, "reduce_umap", lambda e, n, p: [(0.0, 0.0)] * len(e))
    monkeypatch.setattr(wildlife_brain, "cluster_hdbscan", lambda x: ([0] * len(x), [1.0] * len(x)))
    monkeypatch.setattr("app.services.supabase_client.create_service_client", lambda: FakeSvc())
    dim = FakeDino().dim
    part = inference_cache.ResultsPart(header={}, records=[{"media_id": "m1", "dinov3": [0.5] * dim}])
    with inference_cache.use(part):
        out = await wildlife_brain.embed_and_cluster_deployment(DEP)
    resolve.assert_not_awaited()  # no crop downloads at ingest
    assert captured["ids"] == ["m1"] and out["image_count"] == 1
