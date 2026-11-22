# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""The REANNZ cluster's GPU job: lease work from the API, run the models, upload results parts.

    python -m app.jobs.batch_infer --api https://<api> --token-file ~/.config/ww/prod.token --work /work

Run by ``cluster/reannz/ww_infer.sl`` inside the GPU image (Apptainer). It needs no database,
no feature flags and no AWS key: the lease manifest says which models to run on which photos,
originals come from presigned URLs or through the API, and results go up as parts that the
API's ingest step (``jobs/ingest.py``) turns into rows. It asks for a paged lease and reads each
part's photos just before it fetches them (``domain/batch_lease.items_page``); an API from
before paging answers with the photos inline, and those are used as they are. It leases again
until the API has nothing left, then exits 0. A crash leaves the lease to expire; the reaper
requeues the jobs.

Per photo the job records the SpeciesNet prediction, a BioCLIP label for every crop the
pipeline will make (the boxes ``build_speciesnet_observations`` keeps, or the whole frame
where there is none) and, for an embed lease, the DINOv3 vector of the hero crop.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger()

DOWNLOAD_CONCURRENCY = 8
HTTP_TIMEOUT_SECONDS = 120


@dataclass
class Models:
    """Where the models come from; tests pass fakes with the services' interfaces."""

    speciesnet: Callable[[], Any]
    bioclip: Callable[[], Any]
    dinov3: Callable[[Optional[str]], Any]


def default_models() -> Models:
    from app.services.bioclip_service import get_bioclip_service
    from app.services.dinov3 import DinoV3Service
    from app.services.speciesnet_service import get_speciesnet_service

    return Models(speciesnet=get_speciesnet_service, bioclip=get_bioclip_service, dinov3=lambda name: DinoV3Service(name))


# ── the API ──────────────────────────────────────────────────────────


class Api:
    """The lease endpoints. The bearer token goes to the API only, never to a presigned URL."""

    def __init__(self, base_url: str, token: str, *, authed: Any = None, plain: Any = None) -> None:
        import httpx

        self.authed = authed or httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT_SECONDS)
        self.plain = plain or httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)

    async def post(self, path: str, body: dict) -> dict:
        resp = await self.authed.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    async def get(self, path: str, params: dict) -> dict:
        resp = await self.authed.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch(self, item: dict) -> bytes:
        """One photo or crop: a presigned or public URL without the token, an API path with it.

        The API redirects to a presigned copy when an original is too large for its answer;
        the copy is fetched without the token, which goes to the API only.
        """
        if item.get("url"):
            resp = await self.plain.get(item["url"])
        else:
            resp = await self.authed.get(item["path"])
            if resp.is_redirect:
                resp = await self.plain.get(resp.headers["location"])
        resp.raise_for_status()
        return resp.content

    async def put(self, target: dict, data: bytes) -> None:
        headers = target.get("headers") or {}
        resp = await (
            self.plain.put(target["url"], content=data, headers=headers)
            if target.get("url")
            else self.authed.put(target["path"], content=data, headers=headers)
        )
        resp.raise_for_status()

    async def aclose(self) -> None:
        await self.authed.aclose()
        await self.plain.aclose()


# ── inference ────────────────────────────────────────────────────────


async def download(api: Api, items: list[dict], work: Path) -> tuple[list[dict], list[dict]]:
    """Fetch ``items`` into ``work``; returns ``(fetched with "path" and "bytes", failed records)``."""
    sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    fetched: list[dict] = []
    failed: list[dict] = []

    async def _one(item: dict) -> None:
        async with sem:
            try:
                data = await api.fetch(item)
            except Exception as exc:  # noqa: BLE001 - one unreadable photo never sinks the part
                failed.append({"media_id": item["media_id"], "deployment_id": item.get("deployment_id"), "error": f"download: {exc}"})
                return
        path = work / f"{item['media_id']}.jpg"
        await asyncio.to_thread(path.write_bytes, data)
        fetched.append({**item, "local_path": str(path), "bytes": data})

    await asyncio.gather(*[_one(i) for i in items])
    order = {i["media_id"]: n for n, i in enumerate(items)}
    fetched.sort(key=lambda f: order[f["media_id"]])
    return fetched, failed


def crop_boxes(media_id: str, deployment_id: str, pred: Any, config: dict) -> list[tuple[float, float, float, float]]:
    """The boxes of the animal observations ``run_pipeline`` will write for this prediction, as it builds them."""
    from app.domain.pipeline import DetectionCutoffs, bbox_of, build_speciesnet_observations
    from app.services.speciesnet_service import SPECIESNET_VERSION

    rows = build_speciesnet_observations(
        {"id": media_id},
        deployment_id,
        pred,
        SPECIESNET_VERSION,
        datetime.now(timezone.utc).isoformat(),
        float(config.get("confidence_threshold", 0.2)),
        per_detection=bool(config.get("per_detection")),
        cutoffs=DetectionCutoffs.from_config(config),
    )
    boxes = [bbox_of(r) for r in rows if r.get("observation_type") == "animal"]
    return [b for b in boxes if b is not None]


async def infer_annotate(fetched: list[dict], job: dict, models: Models, work: Path) -> list[dict]:
    """SpeciesNet on every photo, then BioCLIP on every crop the pipeline will classify."""
    from app.domain.media_registry import CROP_PADDING
    from app.services import image_processing as imgproc
    from app.services.inference_cache import media_id_of, prediction_to_json

    flags, config = job.get("models") or {}, job.get("config") or {}
    records = {
        f["media_id"]: {"media_id": f["media_id"], "deployment_id": f.get("deployment_id"), "speciesnet": None, "bioclip": []} for f in fetched
    }
    preds: dict[str, Any] = {}
    if flags.get("speciesnet") and fetched:
        for pred in await models.speciesnet().predict([f["local_path"] for f in fetched]):
            preds[media_id_of(pred.filepath)] = pred
            records[media_id_of(pred.filepath)]["speciesnet"] = prediction_to_json(pred)
    if not flags.get("bioclip"):
        return list(records.values())

    crops: list[tuple[str, Optional[tuple[float, float, float, float]], str]] = []
    for f in fetched:
        mid = f["media_id"]
        boxes = crop_boxes(mid, str(f.get("deployment_id")), preds[mid], config) if mid in preds else []
        for n, box in enumerate(boxes):
            path = work / f"{mid}.crop{n}.jpg"
            await asyncio.to_thread(path.write_bytes, imgproc.crop_bbox(f["bytes"], box, CROP_PADDING))
            crops.append((mid, box, str(path)))
        if not boxes and not config.get("per_detection"):
            crops.append((mid, None, f["local_path"]))  # the whole-frame fallback of the BioCLIP step
    if crops:
        labels = config.get("bioclip_labels") or None
        found = await models.bioclip().predict([c[2] for c in crops], labels=labels, rank=config.get("bioclip_rank", "species"))
        by_path = {p.filepath: p for p in found}
        for mid, box, path in crops:
            p = by_path.get(path)
            if p is not None:
                records[mid]["bioclip"].append(
                    {
                        "bbox": list(box) if box else None,
                        "scientific_name": p.scientific_name,
                        "common_name": p.common_name,
                        "score": p.score,
                        "rank": p.rank,
                    }
                )
    return list(records.values())


async def infer_embed(fetched: list[dict], job: dict, models: Models) -> list[dict]:
    """DINOv3 on the hero crops the website stored."""
    if not fetched:
        return []
    model_name = (job.get("models") or {}).get("dinov3")
    vectors = await models.dinov3(model_name).embed([f["bytes"] for f in fetched])
    return [{"media_id": f["media_id"], "deployment_id": f.get("deployment_id"), "dinov3": v} for f, v in zip(fetched, vectors)]


def part_header(lease_id: str, job: dict, part: int) -> dict:
    config, flags = job.get("config") or {}, job.get("models") or {}
    return {
        "lease_id": lease_id,
        "job_id": job["job_id"],
        "part": part,
        "kind": job.get("kind"),
        "image": os.environ.get("WW_GPU_IMAGE", ""),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
        "bioclip_labels": config.get("bioclip_labels"),
        "bioclip_rank": config.get("bioclip_rank"),
        "embedding_model": flags.get("dinov3"),
    }


# ── a lease ──────────────────────────────────────────────────────────


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    cancelled: bool = False


async def _heartbeats(api: Api, lease_id: str, every: float, progress: Progress) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            reply = await api.post(f"/api/worker/lease/{lease_id}/heartbeat", {"done": progress.done, "total": progress.total})
            if reply.get("cancel"):
                logger.warning("batch_lease_cancelled", lease_id=lease_id)
                progress.cancelled = True
                return
        except Exception as exc:  # noqa: BLE001 - a missed beat is retried; the lease outlives a few
            logger.warning("batch_heartbeat_failed", lease_id=lease_id, error=str(exc))


def job_size(job: dict) -> int:
    """Photos in a leased job: its inline ``items``, or the ``items_total`` of a paged one."""
    return len(job["items"]) if "items" in job else int(job.get("items_total") or 0)


async def part_items(api: Api, lease_id: str, job: dict, start: int, size: int) -> list[dict]:
    """The photos of the part that starts at ``start``: inline, or a page signed by the API now."""
    if "items" in job:
        return list(job["items"][start : start + size])
    page = await api.get(f"/api/worker/lease/{lease_id}/items/{job['job_id']}", {"offset": start, "limit": size})
    return list(page.get("items") or [])


async def run_job(api: Api, lease_id: str, job: dict, work: Path, models: Models, part_size: int, progress: Progress) -> dict:
    """All parts of one leased job; returns its report for ``done``."""
    total = job_size(job)
    failed_media: list[str] = []
    parts = 0
    start = 0
    while start < total:
        if progress.cancelled:
            break
        chunk = await part_items(api, lease_id, job, start, part_size)
        if not chunk:
            break
        start += len(chunk)
        fetched, failed = await download(api, chunk, work)
        records = await (infer_embed(fetched, job, models) if job.get("kind") == "embed" else infer_annotate(fetched, job, models, work))
        failed_media.extend(f["media_id"] for f in failed)
        parts += 1
        from app.services.inference_cache import encode_part

        data = encode_part(part_header(lease_id, job, parts), records + failed)
        target = await api.post(f"/api/worker/lease/{lease_id}/part", {"job_id": job["job_id"], "part": parts})
        await api.put(target, data)
        await api.post(f"/api/worker/lease/{lease_id}/part/complete", {"job_id": job["job_id"], "part": parts})
        progress.done += len(chunk)
        for f in fetched:
            for p in work.glob(f"{f['media_id']}*"):
                p.unlink(missing_ok=True)
        logger.info("batch_part_uploaded", lease_id=lease_id, job_id=job["job_id"], part=parts, photos=len(chunk), failed=len(failed))
    return {"job_id": job["job_id"], "parts_total": parts, "failed_media": failed_media}


async def run_lease(api: Api, manifest: dict, work: Path, models: Models) -> None:
    lease_id = manifest["lease_id"]
    progress = Progress(total=sum(job_size(j) for j in manifest.get("jobs") or []))
    beat = asyncio.create_task(_heartbeats(api, lease_id, float(manifest.get("heartbeat_seconds") or 300), progress))
    try:
        reports = [
            await run_job(api, lease_id, job, work, models, int(manifest.get("part_max_photos") or 500), progress)
            for job in manifest.get("jobs") or []
        ]
    finally:
        beat.cancel()
    if not progress.cancelled:
        await api.post(f"/api/worker/lease/{lease_id}/done", {"jobs": reports})


async def run(api: Api, work: Path, models: Optional[Models] = None, max_leases: int = 0) -> int:
    """Lease and run until the API has nothing left (or ``max_leases``); returns the leases run."""
    models = models or default_models()
    work.mkdir(parents=True, exist_ok=True)
    leases = 0
    while not max_leases or leases < max_leases:
        manifest = await api.post("/api/worker/lease", {"slurm_job_id": os.environ.get("SLURM_JOB_ID", ""), "paged": True})
        if not manifest.get("lease_id"):
            break
        logger.info("batch_lease_taken", lease_id=manifest["lease_id"], jobs=len(manifest.get("jobs") or []))
        await run_lease(api, manifest, work, models)
        leases += 1
    logger.info("batch_infer_done", leases=leases)
    return leases


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lease work from the API, run the models, upload results parts.")
    parser.add_argument("--api", required=True, help="API base URL, e.g. https://<id>.execute-api.ap-southeast-6.amazonaws.com")
    parser.add_argument("--token-file", required=True, help="File holding the environment's lease token (mode 600)")
    parser.add_argument("--work", required=True, help="Scratch directory for the photos (nobackup)")
    parser.add_argument("--max-leases", type=int, default=0, help="Stop after this many leases (0: until none is left)")
    args = parser.parse_args(argv)
    token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()

    async def _go() -> int:
        api = Api(args.api.rstrip("/"), token)
        try:
            return await run(api, Path(args.work), max_leases=args.max_leases)
        finally:
            await api.aclose()

    asyncio.run(_go())
    return 0


if __name__ == "__main__":
    sys.exit(main())
