# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""CamtrapDP export with the original photos (#328).

ww-backend's ``export-camtrap-dp`` Edge Function builds the package's metadata and checks who
may have it (``services/camtrap_dp_function.py``). Its media.csv gives each photo the path
``media/<deploymentID>/<mediaID>.<ext>``, the contract between the two repos. Only this
backend can read Google Drive, so this module puts each original at exactly that path and
writes one ZIP on local disk, a photo at a time. ``filePublic`` (false for a photo with a
person in it) is the function's; the export copies media.csv unchanged.

A photo whose original cannot be read stays in media.csv and is named in the package's
description, so the export finishes with what it has rather than failing.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger()

# media/<deploymentID>/<mediaID>.<ext>, as ww-backend's package.ts mediaFilePath() writes it.
MEDIA_PATH = re.compile(r"^media/(?P<deployment>[0-9a-f-]{36})/(?P<media>[0-9a-f-]{36})\.[a-z0-9]+$")

# A failed Drive read is tried this many times, waiting FETCH_BACKOFF_S * attempt between tries.
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_S = 2.0
# Originals read at once. Each is held in memory only until it is written to the ZIP.
FETCH_CONCURRENCY = 6
# Size assumed for a photo when no photo in the export has a recorded size.
ASSUMED_PHOTO_BYTES = 1_000_000

# Where finished exports live in the bucket, how long a link works, and when a ZIP is deleted.
STORAGE_FOLDER = "camtrapdp"
LINK_TTL_SECONDS = 24 * 3600
RETENTION = timedelta(days=7)
# One export at a time per deployment of this backend, so its local disk holds one at most.
EXPORT_LOCK = "camtrapdp-export"

# The description names at most this many missing photos; media.csv lists every one.
_MISSING_NAMED = 20
_PAGE = 1000

Fetch = Callable[[str], Awaitable[Optional[bytes]]]
Progress = Callable[[int, int, int], Awaitable[None]]


class ExportError(Exception):
    """The export cannot be made. The message is shown to the user."""


class ExportTooLarge(ExportError):
    """The photos would not fit under ``CAMTRAPDP_EXPORT_MAX_BYTES``."""


@dataclass(frozen=True)
class ExportSelection:
    """Which part of a project to export: the function's filters."""

    project_id: str
    deployment_ids: list[str] = field(default_factory=list)
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    def function_body(self) -> dict:
        body: dict = {"project_id": self.project_id}
        if self.deployment_ids:
            body["deployment_ids"] = list(self.deployment_ids)
        if self.date_from:
            body["from"] = self.date_from
        if self.date_to:
            body["to"] = self.date_to
        return body


@dataclass(frozen=True)
class ExportCaller:
    """Who the export runs as, which is who the function authorises.

    A signed-in user's JWT, for the Download buttons. Or an organisation, for an organisation
    API key the caller has already validated (#327, ww-backend#290); the function then runs with
    the service role key and refuses a project outside that organisation.
    """

    user_token: Optional[str] = None
    organisation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if bool(self.user_token) == bool(self.organisation_id):
            raise ValueError("An export runs as exactly one of a user or an organisation")


@dataclass(frozen=True)
class Photo:
    media_id: str
    deployment_id: str
    path: str


@dataclass(frozen=True)
class Original:
    """Where a photo's original is stored, and its size when known."""

    file_path: str
    size: Optional[int] = None


@dataclass
class ExportResult:
    photos: int = 0
    written: int = 0
    photo_bytes: int = 0
    # (mediaID, why) for each photo whose original is not in the ZIP.
    missing: list[tuple[str, str]] = field(default_factory=list)


def _human_bytes(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


# ── The metadata package ─────────────────────────────────────────────────


def read_photos(meta: zipfile.ZipFile) -> tuple[list[Photo], list[tuple[str, str]]]:
    """The photos media.csv lists, and any whose filePath breaks the contract (with why).

    A filePath that is not ``media/<its deploymentID>/<its mediaID>.<ext>`` is never written,
    so a changed contract shows up as missing photos rather than files in unexpected places.
    """
    names = set(meta.namelist())
    for required in ("datapackage.json", "media.csv"):
        if required not in names:
            raise ExportError(f"The package from the export function has no {required}.")
    photos: list[Photo] = []
    bad: list[tuple[str, str]] = []
    with meta.open("media.csv") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        if not {"mediaID", "deploymentID", "filePath"} <= set(reader.fieldnames or []):
            raise ExportError("The package's media.csv has no mediaID, deploymentID or filePath column.")
        for row in reader:
            media_id, deployment_id, path = row["mediaID"], row["deploymentID"], row["filePath"]
            m = MEDIA_PATH.match(path or "")
            if not m or m["media"] != media_id or m["deployment"] != deployment_id:
                bad.append((media_id, f"unexpected filePath {path!r}"))
                continue
            photos.append(Photo(media_id, deployment_id, path))
    return photos, bad


def lookup_originals(svc, photos: list[Photo]) -> dict[str, Original]:
    """Each photo's stored original and recorded size, read a deployment at a time.

    Keyset pages ordered by id, because PostgREST returns at most 1,000 rows a request.
    """
    wanted = {p.media_id for p in photos}
    found: dict[str, Original] = {}
    for deployment_id in sorted({p.deployment_id for p in photos}):
        after = None
        while True:
            q = svc.table("media").select("id, file_path, media_assets(file_size_bytes)").eq("deployment_id", deployment_id).is_("deleted_at", "null")
            if after:
                q = q.gt("id", after)
            rows = q.order("id").limit(_PAGE).execute().data or []
            for row in rows:
                if row["id"] in wanted:
                    asset = row.get("media_assets")
                    if isinstance(asset, list):  # PostgREST may nest a to-one as a list
                        asset = asset[0] if asset else None
                    found[row["id"]] = Original(row.get("file_path") or "", (asset or {}).get("file_size_bytes"))
            if len(rows) < _PAGE:
                break
            after = rows[-1]["id"]
    return found


def estimate_bytes(photos: list[Photo], originals: dict[str, Original]) -> int:
    """Total size of the originals: recorded sizes, and their average for the rest."""
    sizes = [o.size for p in photos if (o := originals.get(p.media_id)) and o.size]
    known = sum(sizes)
    average = known / len(sizes) if sizes else ASSUMED_PHOTO_BYTES
    return int(known + average * (len(photos) - len(sizes)))


def check_size(estimate: int, max_bytes: int) -> None:
    if estimate > max_bytes:
        raise ExportTooLarge(
            f"These photos come to about {_human_bytes(estimate)}, more than the {_human_bytes(max_bytes)} "
            "one export can hold. Export fewer deployments or a shorter date range."
        )


def note_missing(descriptor: dict, missing: list[tuple[str, str]]) -> dict:
    """Add the photos whose originals are not in the ZIP to the package's description."""
    if not missing:
        return descriptor
    ids = [media_id for media_id, _ in missing]
    named = ", ".join(ids[:_MISSING_NAMED]) + (f" and {len(ids) - _MISSING_NAMED} others" if len(ids) > _MISSING_NAMED else "")
    note = (
        f"Original photo not included: {len(ids)} photo(s) whose original could not be read at export, "
        f"so media.csv lists them but media/ has no file for them: {named}."
    )
    out = dict(descriptor)
    out["description"] = "\n\n".join(part for part in (descriptor.get("description"), note) if part)
    return out


# ── Writing the ZIP ──────────────────────────────────────────────────────


def _readable(file_path: str) -> bool:
    return file_path.startswith(("gdrive://", "https://", "http://"))


async def fetch_with_retries(file_path: str, fetch: Fetch, *, attempts: int = FETCH_ATTEMPTS, backoff_s: Optional[float] = None) -> Optional[bytes]:
    """The original's bytes, or None once ``attempts`` reads have failed."""
    backoff_s = FETCH_BACKOFF_S if backoff_s is None else backoff_s
    for attempt in range(1, attempts + 1):
        try:
            data = await fetch(file_path)
        except Exception as exc:  # noqa: BLE001 - one unreadable photo must not end the export
            logger.warning("camtrapdp_export_fetch_failed", attempt=attempt, error=str(exc))
            data = None
        if data:
            return data
        if attempt < attempts:
            await asyncio.sleep(backoff_s * attempt)
    return None


async def write_package(
    meta_path: Path,
    out_path: Path,
    photos: list[Photo],
    originals: dict[str, Original],
    *,
    fetch: Fetch,
    max_bytes: int,
    on_progress: Optional[Progress] = None,
    missing: Optional[list[tuple[str, str]]] = None,
    concurrency: int = FETCH_CONCURRENCY,
    backoff_s: Optional[float] = None,
) -> ExportResult:
    """Write the package with its originals to ``out_path``.

    Every original goes to its media.csv path, stored rather than compressed (JPEGs do not
    shrink). The metadata files follow, copied unchanged except datapackage.json, which gains
    the missing photos. ``missing`` starts the list with photos already known to be missing.
    """
    result = ExportResult(photos=len(photos) + len(missing or []), missing=list(missing or []))
    pending = iter(photos)
    write_lock = asyncio.Lock()
    done = 0

    with zipfile.ZipFile(meta_path) as meta, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:

        async def worker() -> None:
            nonlocal done
            for photo in pending:  # one shared iterator: each photo goes to one worker
                original = originals.get(photo.media_id)
                data, why = None, None
                if original is None:
                    why = "not found in the database"
                elif not _readable(original.file_path):
                    why = "no stored original"
                else:
                    data = await fetch_with_retries(original.file_path, fetch, backoff_s=backoff_s)
                    why = None if data else "could not be read from storage"
                async with write_lock:
                    if data is not None:
                        check_size(result.photo_bytes + len(data), max_bytes)
                        await asyncio.to_thread(out.writestr, photo.path, data, zipfile.ZIP_STORED)
                        result.written += 1
                        result.photo_bytes += len(data)
                    else:
                        result.missing.append((photo.media_id, why))
                    done += 1
                    if on_progress:
                        await on_progress(done, len(photos), len(result.missing))

        tasks = [asyncio.create_task(worker()) for _ in range(max(1, min(concurrency, len(photos))))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for name in meta.namelist():
            if name == "datapackage.json" or name.startswith("media/") or name.endswith("/"):
                continue
            with meta.open(name) as src, out.open(name, "w") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
        descriptor = json.loads(meta.read("datapackage.json"))
        out.writestr("datapackage.json", json.dumps(note_missing(descriptor, result.missing), indent=2))

    return result


# ── The whole export ─────────────────────────────────────────────────────


def export_filename(project_id: str, when: Optional[datetime] = None) -> str:
    return f"camtrapdp-{project_id}-{(when or datetime.now(timezone.utc)).date().isoformat()}.zip"


def storage_path(job_id: str) -> str:
    return f"{STORAGE_FOLDER}/{job_id}.zip"


async def build_export(
    selection: ExportSelection,
    caller: ExportCaller,
    workdir: Path,
    *,
    max_bytes: int,
    on_message: Callable[[str, float], Awaitable[None]],
    on_progress: Optional[Progress] = None,
    fetch: Optional[Fetch] = None,
) -> tuple[Path, ExportResult]:
    """Make the export ZIP in ``workdir``; returns its path and what went into it.

    The function is called first, while a user's token is fresh. Only then does the export wait
    its turn for the disk (``EXPORT_LOCK``).
    """
    from app.services import camtrap_dp_function as fn
    from app.services.locks import exclusive
    from app.services.supabase_client import create_service_client

    meta_path = workdir / "metadata.zip"
    try:
        await fn.download_package(selection.function_body(), meta_path, user_token=caller.user_token)
    except fn.CamtrapFunctionError as exc:
        if exc.status == 401:
            raise ExportError("Your sign-in expired before the export started. Start it again.") from exc
        raise ExportError(exc.message) from exc
    try:
        with zipfile.ZipFile(meta_path) as meta:
            photos, bad = read_photos(meta)
    except zipfile.BadZipFile as exc:
        raise ExportError("The export function did not return a ZIP.") from exc

    originals = await asyncio.to_thread(lookup_originals, create_service_client(), photos)
    estimate = estimate_bytes(photos, originals)
    check_size(estimate, max_bytes)
    await on_message(f"{len(photos)} photos, about {_human_bytes(estimate)}. Waiting for the export queue…", 0.05)

    async with exclusive(EXPORT_LOCK):
        await on_message(f"Adding {len(photos)} original photos…", 0.1)
        result = await write_package(
            meta_path,
            workdir / "export.zip",
            photos,
            originals,
            fetch=fetch or _fetch_original,
            max_bytes=max_bytes,
            on_progress=on_progress,
            missing=bad,
        )
    meta_path.unlink(missing_ok=True)
    return workdir / "export.zip", result


async def _fetch_original(file_path: str) -> Optional[bytes]:
    from app.domain.media_resolver import resolve_media

    resolved = await resolve_media(file_path, size="full")
    return resolved[0] if resolved else None


async def store_export(zip_path: Path, job_id: str, filename: str, bucket: str) -> str:
    """Upload the ZIP to the private bucket and return a link that works for a day.

    Also deletes exports older than ``RETENTION``, best-effort.
    """
    from app.services.storage import delete_older_than, signed_download_url, upload_file_to_storage

    path = storage_path(job_id)
    if not await upload_file_to_storage(bucket, path, zip_path, "application/zip"):
        raise ExportError("The export could not be saved. Try again later.")
    url = await asyncio.to_thread(signed_download_url, bucket, path, LINK_TTL_SECONDS, filename)
    if not url:
        raise ExportError("The export was saved but no download link could be made. Try again later.")
    removed = await delete_older_than(bucket, STORAGE_FOLDER, datetime.now(timezone.utc) - RETENTION)
    if removed:
        logger.info("camtrapdp_exports_expired", removed=removed)
    return url
