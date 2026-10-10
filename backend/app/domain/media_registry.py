# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media Registry — storage-agnostic URL resolution + rendition generation.

The UI never reads ``storage_key`` or guesses where a file lives. It calls
``resolve_url`` (a thumbnail/preview URL served from the public Supabase Storage
bucket, or a public original) and lets the registry hide the storage provider.

Generation (thumbnails, previews, animal crops) writes the small derivatives to
the public ``media-renditions`` Supabase Storage bucket and records the URLs in
the ``media_assets`` side table (1:1 with ``media``), keeping the mobile-synced
``media`` table lean. Originals stay in Google Drive (free, 100 TB).

Layering: domain orchestration here; Pillow + Supabase Storage live in services.
No FastAPI imports.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

import structlog

from app.services.db_utils import row_of, rows_of

logger = structlog.get_logger()

THUMBNAIL_MAX = 300  # px, longest edge
PREVIEW_MAX = 800
CROP_PADDING = 0.1

_PREP_ATTEMPTS = 3
_PREP_BACKOFF_SECONDS = 1.0  # waits 1 s, then 2 s
_FETCH_PAGE = 1000
_PROGRESS_EVERY = 25


class RenditionUploadError(RuntimeError):
    """A rendition could not be written to storage."""


# ── URL resolution (pure) ────────────────────────────────────────────


def _assets(media_row: dict) -> dict:
    """Extract the 1:1 media_assets record (PostgREST nests it as list or dict)."""
    a = media_row.get("media_assets")
    if isinstance(a, list):
        a = a[0] if a else None
    return a or {}


def _public_original_url(media_row: dict) -> Optional[str]:
    """The original's URL when it is public (http/https), else None.

    A private original (``gdrive://`` and the like) is only reachable through the
    auth-gated ``/api/media/{id}/image`` proxy, which a plain ``<img>`` cannot load
    (no Authorization header, #305), so the registry never hands that URL out. A
    client that holds a token fetches the proxy itself.
    """
    file_path = media_row.get("file_path") or ""
    if file_path.startswith(("http://", "https://")):
        return file_path
    return None


def resolve_url(media_row: dict, size: str = "thumbnail") -> Optional[str]:
    """Resolve a media row to a URL a plain ``<img>`` can load, or None.

    Fallback chains:
    - ``thumbnail`` → thumbnail_url → preview_url → public original
    - ``preview``   → preview_url   → public original (never the tiny thumbnail)
    - ``original``  → public original

    None means there is no rendition yet and the original is private: the UI shows
    its no-thumbnail placeholder.
    """
    assets = _assets(media_row)
    if size == "thumbnail":
        return assets.get("thumbnail_url") or assets.get("preview_url") or _public_original_url(media_row)
    if size == "preview":
        return assets.get("preview_url") or _public_original_url(media_row)
    return _public_original_url(media_row)


def with_resolved_urls(media_row: dict) -> dict:
    """Return the row plus pre-resolved thumbnail/preview/original URLs, each possibly None."""
    return {
        **media_row,
        "thumbnail_url": resolve_url(media_row, "thumbnail"),
        "preview_url": resolve_url(media_row, "preview"),
        "original_url": resolve_url(media_row, "original"),
    }


# ── Rendition generation (orchestration) ─────────────────────────────


async def _upsert_media_assets(patch: dict) -> None:
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _do():
        svc.table("media_assets").upsert(patch, on_conflict="media_id").execute()

    await asyncio.to_thread(_do)


async def prepare_media_assets(media_row: dict) -> dict:
    """Generate thumbnail + preview for one media row and upsert media_assets.

    Returns the patch written (empty dict if the original could not be resolved).
    """
    from app.domain.media_resolver import resolve_media
    from app.services import image_processing as imgproc
    from app.services.storage import upload_rendition

    media_id = media_row["id"]
    deployment_id = media_row["deployment_id"]

    resolved = await resolve_media(media_row.get("file_path", ""), size="full")
    if not resolved:
        logger.warning("media_prep_unresolvable", media_id=media_id)
        return {}
    data, _content_type = resolved

    def _renditions():
        width, height = imgproc.get_dimensions(data)
        return width, height, imgproc.resize_to_max(data, THUMBNAIL_MAX), imgproc.resize_to_max(data, PREVIEW_MAX)

    width, height, thumb, preview = await asyncio.to_thread(_renditions)

    thumb_url = await upload_rendition(f"thumbnails/{deployment_id}/{media_id}.jpg", thumb)
    if not thumb_url:
        # upload_rendition already logged why; raise so the caller can retry.
        raise RenditionUploadError(f"thumbnail upload failed for media {media_id}")
    preview_url = await upload_rendition(f"previews/{deployment_id}/{media_id}.jpg", preview)

    # NOTE: storage_provider/storage_key describe the *original* file's location and must
    # be set as a pair (chk_storage_complete). Renditions live in Supabase Storage and are
    # addressed by the *_url columns, so we leave both NULL here rather than half-populating.
    patch = {
        "media_id": media_id,
        "thumbnail_url": thumb_url,
        "preview_url": preview_url,
        "file_size_bytes": len(data),
        "original_width": width,
        "original_height": height,
    }
    await _upsert_media_assets(patch)
    return patch


def is_permission_error(exc: BaseException) -> bool:
    """True when the database refused the write (Postgres 42501), which no retry can fix."""
    return getattr(exc, "code", None) == "42501" or "42501" in str(exc) or "permission denied" in str(exc).lower()


async def prepare_media_assets_with_retry(media_row: dict, attempts: int = _PREP_ATTEMPTS) -> dict:
    """:func:`prepare_media_assets`, retried with a short backoff on transient errors.

    A permission error is raised at once: it is the same for every attempt and every
    photo, so callers stop instead of hammering the database.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await prepare_media_assets(media_row)
        except Exception as exc:
            if is_permission_error(exc) or attempt == attempts:
                raise
            logger.info("media_prep_retry", media_id=media_row.get("id"), attempt=attempt, error=str(exc))
            await asyncio.sleep(_PREP_BACKOFF_SECONDS * attempt)
    return {}  # unreachable: the loop returns or raises


async def generate_observation_crops(media_id: str) -> Optional[str]:
    """Crop every AI animal detection on a frame, one crop per observation.

    Writes each detection's bbox crop to ``observations.crop_url`` (so multi-animal
    frames get a crop per box) and points the media's hero
    ``media_assets.animal_crop_url`` at the highest-confidence crop. The source
    frame is fetched once and reused for all boxes.

    Returns the hero crop URL, or ``None`` when there's nothing to crop, so the
    pipeline can tell which frames were handled (and fall back to motion ROI).
    """
    from app.domain.media_resolver import resolve_media
    from app.services import image_processing as imgproc
    from app.services.storage import upload_rendition
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        media = svc.table("media").select("id, deployment_id, file_path").eq("id", media_id).maybe_single().execute()
        obs = (
            svc.table("observations")
            .select("id, bbox_x, bbox_y, bbox_w, bbox_h, confidence")
            .eq("media_id", media_id)
            .eq("source_type", "ai")
            .eq("observation_type", "animal")
            .not_.is_("bbox_x", "null")
            .order("confidence", desc=True)  # first row → hero
            .execute()
        )
        return row_of(media), rows_of(obs)

    media_row, obs_rows = await asyncio.to_thread(_fetch)
    if not media_row or not obs_rows:
        return None

    resolved = await resolve_media(media_row["file_path"], size="full")
    if not resolved:
        return None
    data, _content_type = resolved

    deployment_id = media_row["deployment_id"]

    def _set_crop_url(obs_id: str, url: str):
        svc.table("observations").update({"crop_url": url}).eq("id", obs_id).execute()

    hero_url: Optional[str] = None
    for o in obs_rows:
        bbox = (o["bbox_x"], o["bbox_y"], o["bbox_w"], o["bbox_h"])
        crop = await asyncio.to_thread(imgproc.crop_bbox, data, bbox, CROP_PADDING)
        url = await upload_rendition(f"crops/{deployment_id}/{media_id}/{o['id']}.jpg", crop)
        if not url:
            continue
        await asyncio.to_thread(_set_crop_url, o["id"], url)
        if hero_url is None:
            hero_url = url

    if hero_url:
        await _upsert_media_assets({"media_id": media_id, "animal_crop_url": hero_url})
    return hero_url


def group_bursts(media_rows: list[dict], gap_seconds: float) -> list[list[dict]]:
    """Trigger bursts of ``media_rows``: the one grouper, ``domain.burst_evidence.group_bursts``.

    Kept under this name for the motion-ROI caller. Frames with an unparseable or
    missing timestamp are singletons; singletons are dropped by the crop caller
    (motion ROI needs at least two frames) but kept here so the function is
    purely structural.
    """
    from app.domain.burst_evidence import group_bursts as _group

    return _group(media_rows, gap_seconds)


async def generate_motion_roi_crops(
    deployment_id: str,
    media_rows: list[dict],
    *,
    skip_media_ids: Optional[set[str]] = None,
    burst_gap_seconds: float = 10.0,
) -> int:
    """SpeciesNet-free fallback crop: localise the moving subject per burst via frame differencing.

    Groups ``media_rows`` (already timestamp-ordered) into bursts, computes a per-frame motion
    ROI (pure numpy + Pillow, no ML), and writes ``animal_crop_url`` for frames that don't already
    have a detection-based crop — so DINOv3 still receives an animal region when SpeciesNet is
    unavailable. Returns the number of crops created.

    Only image media participate; frames in ``skip_media_ids`` keep their place in the sequence
    (they still inform the differencing) but are never overwritten.
    """
    from io import BytesIO

    from PIL import Image

    from app.domain.media_resolver import resolve_media
    from app.domain.motion_roi import compute_motion_roi_per_frame
    from app.services import image_processing as imgproc
    from app.services.storage import upload_rendition

    skip = skip_media_ids or set()
    crops_created = 0

    for burst in group_bursts(media_rows, burst_gap_seconds):
        if len(burst) < 2:
            continue  # motion ROI needs at least two frames to difference

        # Download every frame once; keep raw bytes (for cropping) and a decoded image (for ROI).
        frames: list[tuple[dict, Optional[bytes], Optional[Image.Image]]] = []
        for m in burst:
            data = img = None
            try:
                resolved = await resolve_media(m["file_path"], size="full")
                if resolved:
                    data = resolved[0]
                    img = await asyncio.to_thread(lambda d=data: Image.open(BytesIO(d)).convert("RGB"))
            except Exception as exc:  # noqa: BLE001 — a single bad frame must not sink the burst
                logger.warning("motion_roi_resolve_error", media_id=m.get("id"), error=str(exc))
            frames.append((m, data, img))

        images = [img for (_m, _data, img) in frames]
        if sum(im is not None for im in images) < 2:
            continue

        rois = await asyncio.to_thread(compute_motion_roi_per_frame, images)

        for (m, data, img), roi in zip(frames, rois):
            if roi is None or img is None or data is None or m["id"] in skip:
                continue
            x0, y0, x1, y1 = roi
            w, h = img.width, img.height
            # Pixel ROI → normalised (x, y, w, h). The ROI is already padded by compute_motion_roi,
            # so crop with zero extra padding.
            norm = (x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)
            try:
                crop = await asyncio.to_thread(imgproc.crop_bbox, data, norm, 0.0)
                crop_url = await upload_rendition(f"crops/{deployment_id}/{m['id']}.jpg", crop)
                await _upsert_media_assets({"media_id": m["id"], "animal_crop_url": crop_url})
                crops_created += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("motion_roi_crop_error", media_id=m.get("id"), error=str(exc))

    if crops_created:
        logger.info("motion_roi_fallback_crops", deployment_id=deployment_id, crops_created=crops_created)
    return crops_created


async def backfill_thumbnails(
    deployment_id: str,
    progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> int:
    """Generate thumbnails/previews for deployment media that lack them.

    Each photo gets :func:`prepare_media_assets_with_retry`. A permission error
    aborts the whole run, since every other photo would be refused the same way.
    ``progress(done, total)`` is awaited every ``_PROGRESS_EVERY`` photos.

    Returns the number of media rows for which a thumbnail was produced.
    """
    from app.services.supabase_client import create_service_client

    svc = create_service_client()

    def _fetch():
        # PostgREST caps a response at 1,000 rows, so page through.
        rows: list[dict] = []
        while True:
            resp = (
                svc.table("media")
                .select("id, deployment_id, file_path, media_assets(thumbnail_url)")
                .eq("deployment_id", deployment_id)
                .is_("deleted_at", "null")
                .order("id")
                .range(len(rows), len(rows) + _FETCH_PAGE - 1)
                .execute()
            )
            page = rows_of(resp)
            rows.extend(page)
            if len(page) < _FETCH_PAGE:
                return rows

    rows = await asyncio.to_thread(_fetch)
    missing = [r for r in rows if not _assets(r).get("thumbnail_url")]
    generated = failed = 0
    for done, row in enumerate(missing, start=1):
        try:
            patch = await prepare_media_assets_with_retry(row)
            if patch.get("thumbnail_url"):
                generated += 1
            else:
                failed += 1
        except Exception as exc:
            if is_permission_error(exc):
                logger.error("backfill_thumbnails_refused", deployment_id=deployment_id, media_id=row.get("id"), error=str(exc))
                raise
            failed += 1
            logger.warning("backfill_thumbnail_failed", media_id=row.get("id"), error=str(exc))
        if progress and (done % _PROGRESS_EVERY == 0 or done == len(missing)):
            await progress(done, len(missing))
    logger.info(
        "backfill_thumbnails_complete",
        deployment_id=deployment_id,
        generated=generated,
        failed=failed,
        missing=len(missing),
        total=len(rows),
    )
    return generated
