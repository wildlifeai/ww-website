# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Google Drive upload service — service-account based.

Uploads analysis images to a shared Google Drive folder, organised by
project and deployment. Uses SHA-256 hashing via ``appProperties`` to
prevent duplicate uploads.

Folder structure::

    <root>
    ├── {slug(project_name)}_{project_id[:8]}
    │   └── {YYYY-MM-DD}_{deployment_id[:8]}
    │       └── {timestamp}_{original_filename}

One folder per deployment, holding all of its photos. The date prefix is
the deployment **end** date (start date while the deployment is still
active), so folders sort chronologically by when the deployment finished;
the id suffix distinguishes deployments that ended on the same day.

Deployment folders carry ``appProperties.deployment_id`` so the folder can
be found again by identity, not name: when new photos arrive for a
deployment whose folder was created while it was still active, the folder
is reused (and renamed to the end date) instead of a second one appearing.
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import settings

logger = structlog.get_logger()

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Max concurrent uploads per job
MAX_CONCURRENT_UPLOADS = 5


# ── Helpers ──────────────────────────────────────────────────────────


def slugify(name: str, max_length: int = 50) -> str:
    """Convert a project name to a Drive-safe folder slug.

    - lowercase
    - replace non-alphanumeric chars with hyphens
    - collapse multiple hyphens
    - trim to max_length
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_length]


def build_deployment_folder_name(
    deployment_start: Optional[str],
    deployment_end: Optional[str],
    deployment_id: str,
) -> str:
    """Canonical deployment folder name: ``{YYYY-MM-DD}_{deployment_id[:8]}``.

    The date is the deployment **end** date; while the deployment is still
    active (no end date) the start date is used, falling back to today (UTC)
    when both are missing. The ISO date prefix makes folders sort by when the
    deployment finished; the id suffix disambiguates same-day deployments.
    """
    date_source = deployment_end or deployment_start
    if date_source:
        date_part = str(date_source)[:10]
    else:
        from datetime import datetime, timezone

        date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{date_part}_{deployment_id[:8]}"


def compute_file_hash(file_bytes: bytes, deployment_id: str) -> str:
    """SHA-256 hash of file content + deployment ID for dedup."""
    h = hashlib.sha256()
    h.update(file_bytes)
    h.update(deployment_id.encode("utf-8"))
    return h.hexdigest()


def sanitize_filename(timestamp: Optional[str], original_name: str) -> str:
    """Build a Drive filename: ``{timestamp}_{original_name}``.

    If no timestamp is available, uses the original name only.
    Replaces colons (invalid in some FS) with hyphens.
    """
    if timestamp:
        safe_ts = timestamp.replace(":", "-").replace(" ", "T")
        return f"{safe_ts}_{original_name}"
    return original_name


# ── Service ──────────────────────────────────────────────────────────


class DriveTransientError(Exception):
    """A Drive upload failure worth retrying: a timeout, a dropped connection, or a 429/5xx."""


# A single frame upload is retried this many times on a transient failure, waiting
# DRIVE_UPLOAD_BACKOFF_S * attempt between tries. The first bench run of the
# person-detection e2e lost one frame in ten to a single 60 s read timeout that a
# second attempt would have carried.
DRIVE_UPLOAD_ATTEMPTS = 3
DRIVE_UPLOAD_BACKOFF_S = 2.0
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


class GoogleDriveService:
    """Stateless Google Drive API wrapper.

    All folder-ID lookups go through Redis cache first; only falls back
    to a Drive API ``files.list`` query on cache miss.
    """

    def __init__(self):
        creds = self._load_credentials()
        self._credentials = creds
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._api_lock = asyncio.Lock()
        # Per-instance deployment-folder memo (instances are per-job): the
        # find/rename/create resolution runs once per deployment per batch.
        self._dep_folder_memo: Dict[str, str] = {}

    # ── Auth ─────────────────────────────────────────────────────

    @staticmethod
    def _load_credentials() -> service_account.Credentials:
        """Load service-account credentials from file path or inline JSON."""
        raw = (settings.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
        if not raw:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set — cannot authenticate with Google Drive")

        # Inline JSON (e.g. production secret) starts with '{'; anything else is a file path.
        # This avoids json.loads() choking on a misconfigured path and emitting a cryptic
        # "Expecting value: line 1 column 1 (char 0)" — surface an actionable message instead.
        if raw.startswith("{"):
            info = json.loads(raw)
        else:
            path = Path(raw)
            if not path.is_file():
                raise RuntimeError(
                    f"GOOGLE_SERVICE_ACCOUNT_JSON points to a file that does not exist in this "
                    f"environment: '{raw}'. In Docker, start the API with the dev compose so the "
                    "service account is mounted and the path is set to /app/service-account.json:\n"
                    "  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d api\n"
                    "Or set GOOGLE_SERVICE_ACCOUNT_JSON to the inline service-account JSON."
                )
            info = json.loads(path.read_text(encoding="utf-8"))

        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    # ── Redis cache helpers ──────────────────────────────────────

    @staticmethod
    async def _get_cached_folder(cache_key: str) -> Optional[str]:
        """Try to read a folder ID from Redis. Returns None on miss / error."""
        if not settings.REDIS_URL:
            return None
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            value = await r.get(cache_key)
            await r.close()
            return value.decode("utf-8") if value else None
        except Exception:
            return None

    @staticmethod
    async def _set_cached_folder(cache_key: str, folder_id: str) -> None:
        """Store a folder ID in Redis with 24h TTL."""
        if not settings.REDIS_URL:
            return
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            await r.set(cache_key, folder_id, ex=86400)
            await r.close()
        except Exception:
            pass  # cache miss is fine — next call will re-query Drive

    # ── Folder management ────────────────────────────────────────

    def _find_folder(self, parent_id: str, name: str) -> Optional[str]:
        """Search for an existing folder by name under *parent_id*."""
        query = f"'{parent_id}' in parents and name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = (
            self._service.files()
            .list(q=query, fields="files(id)", spaces="drive", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def _create_folder(self, parent_id: str, name: str, app_properties: Optional[Dict[str, str]] = None) -> str:
        """Create a new folder and return its ID."""
        metadata: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        if app_properties:
            metadata["appProperties"] = app_properties
        folder = self._service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
        return folder["id"]

    def _find_folder_by_id_suffix(self, parent_id: str, deployment_id: str) -> Optional[str]:
        """Find an untagged legacy deployment folder by its ``*_{id[:8]}`` name suffix.

        Folders created before appProperties tagging are named with a date prefix
        that may since have changed (start date → end date), so a lookup by the
        current desired name misses them. The 8-char deployment-id suffix is
        stable across renames, so match on that instead.
        """
        suffix = f"_{deployment_id[:8]}"
        query = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and name contains '{suffix}' and trashed = false"
        results = (
            self._service.files()
            .list(q=query, fields="files(id, name)", spaces="drive", pageSize=10, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        # `contains` matches anywhere in the name — keep only true suffix matches.
        for f in results.get("files", []):
            if f.get("name", "").endswith(suffix):
                return f["id"]
        return None

    def _find_folder_by_deployment_id(self, parent_id: str, deployment_id: str) -> Optional[Dict[str, str]]:
        """Find a deployment folder by its ``appProperties.deployment_id`` tag.

        Returns ``{"id": ..., "name": ...}`` or None. Identity-based lookup —
        immune to the folder's date prefix changing when the deployment ends.
        """
        query = (
            f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' "
            f"and appProperties has {{ key='deployment_id' and value='{deployment_id}' }} and trashed = false"
        )
        results = (
            self._service.files()
            .list(q=query, fields="files(id, name)", spaces="drive", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        files = results.get("files", [])
        return files[0] if files else None

    def _patch_folder(self, folder_id: str, *, name: Optional[str] = None, app_properties: Optional[Dict[str, str]] = None) -> None:
        """Update a folder's name and/or appProperties."""
        body: Dict[str, Any] = {}
        if name:
            body["name"] = name
        if app_properties:
            body["appProperties"] = app_properties
        if body:
            self._service.files().update(fileId=folder_id, body=body, supportsAllDrives=True).execute()

    async def ensure_folder(self, parent_id: str, name: str, cache_key: Optional[str] = None) -> str:
        """Find or create a folder, with optional Redis caching.

        This is run in a thread pool because the Drive SDK is synchronous.
        """
        if cache_key:
            cached = await self._get_cached_folder(cache_key)
            if cached:
                return cached

        async with self._api_lock:
            folder_id = await asyncio.to_thread(self._find_folder, parent_id, name)
            if not folder_id:
                folder_id = await asyncio.to_thread(self._create_folder, parent_id, name)
                logger.info("drive_folder_created", name=name, folder_id=folder_id)

        if cache_key:
            await self._set_cached_folder(cache_key, folder_id)

        return folder_id

    async def ensure_deployment_folder(self, parent_id: str, deployment_id: str, desired_name: str) -> str:
        """Find or create the single folder for a deployment, by identity.

        Resolution order:
        1. Folder tagged ``appProperties.deployment_id`` — the deployment's
           existing folder, even if its date prefix is stale (created while
           the deployment was active). Renamed to *desired_name* so the
           prefix reflects the end date once the deployment finishes.
        2. Folder with the desired name (created before tagging existed) —
           adopted by tagging it with the deployment id.
        3. Untagged folder whose name ends in ``_{deployment_id[:8]}`` — a
           legacy folder whose date prefix is stale (e.g. created with the
           start date while the deployment was active). Adopted by tagging it
           and renaming to *desired_name*; without this, the rename-on-end
           name change made a second folder appear for the same deployment.
        4. Otherwise created, tagged with the deployment id.

        No Redis cache here (unlike :meth:`ensure_folder`): resolving through
        Drive once per job is what keeps the rename-on-end behaviour working.
        """
        memoised = self._dep_folder_memo.get(deployment_id)
        if memoised:
            return memoised

        async with self._api_lock:
            # Re-check under the lock — another file of the same deployment
            # may have resolved the folder while we waited.
            memoised = self._dep_folder_memo.get(deployment_id)
            if memoised:
                return memoised

            existing = await asyncio.to_thread(self._find_folder_by_deployment_id, parent_id, deployment_id)
            if existing:
                folder_id = existing["id"]
                if existing.get("name") != desired_name:
                    await asyncio.to_thread(self._patch_folder, folder_id, name=desired_name)
                    logger.info(
                        "drive_deployment_folder_renamed",
                        deployment_id=deployment_id,
                        old_name=existing.get("name"),
                        new_name=desired_name,
                    )
            else:
                folder_id = await asyncio.to_thread(self._find_folder, parent_id, desired_name)
                if folder_id:
                    await asyncio.to_thread(self._patch_folder, folder_id, app_properties={"deployment_id": deployment_id})
                else:
                    # Legacy folder with a stale date prefix (pre-tagging) — adopt and rename.
                    folder_id = await asyncio.to_thread(self._find_folder_by_id_suffix, parent_id, deployment_id)
                    if folder_id:
                        await asyncio.to_thread(self._patch_folder, folder_id, name=desired_name, app_properties={"deployment_id": deployment_id})
                        logger.info(
                            "drive_legacy_folder_adopted",
                            deployment_id=deployment_id,
                            folder_id=folder_id,
                            new_name=desired_name,
                        )
                    else:
                        folder_id = await asyncio.to_thread(self._create_folder, parent_id, desired_name, {"deployment_id": deployment_id})
                        logger.info("drive_folder_created", name=desired_name, folder_id=folder_id)

            self._dep_folder_memo[deployment_id] = folder_id

        return folder_id

    # ── Deduplication ────────────────────────────────────────────

    def _find_file_id_by_hash(self, parent_id: str, file_hash: str) -> Optional[str]:
        """Return the id of an existing file with this hash in the folder, or None.

        Returning the id (not just a bool) lets the caller register a media row for a
        Drive-skipped duplicate that has no DB row yet — see Guard 1 in the upload job.
        """
        query = f"'{parent_id}' in parents and appProperties has {{ key='sha256' and value='{file_hash}' }} and trashed = false"
        results = (
            self._service.files()
            .list(q=query, fields="files(id)", spaces="drive", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        files = results.get("files", [])
        return files[0]["id"] if files else None

    # ── File upload ──────────────────────────────────────────────

    async def upload_file(
        self,
        parent_id: str,
        filename: str,
        file_bytes: bytes,
        mime_type: str,
        file_hash: str,
    ) -> tuple[Optional[str], bool]:
        """Upload a single file to Google Drive.

        Returns ``(file_id, was_new)``. ``was_new`` is False when the file already
        existed in Drive (dedup) — the existing id is still returned so the caller can
        register/patch a media row for it.

        Transient failures (timeouts, dropped connections, HTTP 429/5xx) are
        retried up to DRIVE_UPLOAD_ATTEMPTS times; anything else raises at once.
        """
        # Dedup check — returns the existing file id if present.
        async with self._api_lock:
            existing_id = await asyncio.to_thread(self._find_file_id_by_hash, parent_id, file_hash)

        if existing_id:
            logger.info("drive_upload_skipped_duplicate", filename=filename)
            return existing_id, False

        def _do_upload() -> str:
            import json

            import google.auth.transport.requests
            import requests

            # Ensure credentials are fresh to get a valid token
            req = google.auth.transport.requests.Request()
            self._credentials.refresh(req)
            access_token = self._credentials.token

            metadata: Dict[str, Any] = {
                "name": filename,
                "parents": [parent_id],
                "appProperties": {"sha256": file_hash},
            }

            headers = {"Authorization": f"Bearer {access_token}"}
            multipart_files = {"metadata": ("metadata", json.dumps(metadata), "application/json"), "file": (filename, file_bytes, mime_type)}

            try:
                resp = requests.post(
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id",
                    headers=headers,
                    files=multipart_files,
                    timeout=60,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                raise DriveTransientError(f"{type(exc).__name__}: {exc}") from exc

            if resp.status_code in _TRANSIENT_HTTP:
                raise DriveTransientError(f"Drive Upload Error HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code not in (200, 201):
                raise Exception(f"Drive Upload Error HTTP {resp.status_code}: {resp.text}")

            return resp.json()["id"]

        for attempt in range(1, DRIVE_UPLOAD_ATTEMPTS + 1):
            try:
                file_id = await asyncio.to_thread(_do_upload)
                break
            except DriveTransientError as exc:
                if attempt == DRIVE_UPLOAD_ATTEMPTS:
                    raise
                delay = DRIVE_UPLOAD_BACKOFF_S * attempt
                logger.warning(
                    "drive_file_upload_retry",
                    filename=filename,
                    attempt=attempt,
                    retry_in_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        logger.info("drive_file_uploaded", filename=filename, file_id=file_id)
        return file_id, True

    # ── Batch orchestration ──────────────────────────────────────

    async def upload_analysis_images(
        self,
        files: List[Dict[str, Any]],
        file_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Upload a batch of images to their correct distinct Drive folders.

        Parameters
        ----------
        files : list of dicts
            Each dict has ``storage_path``, ``filename``, ``timestamp``,
            ``file_bytes``, ``project``, and ``deployment``.
        file_callback : callable, optional
            Awaitable called with keyword arguments for each event::

                await file_callback(action="uploaded", filename="...", index=5, total=38)
                await file_callback(action="skipped",  filename="...", index=5, total=38)
                await file_callback(action="failed",   filename="...", index=5, total=38, error="...")
                await file_callback(action="folder_created", folder_name="...")

        Returns
        -------
        dict with ``uploaded``, ``skipped``, ``failed`` counts.
        """
        root_folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
        if not root_folder_id:
            # Without a root, Drive would create the project/deployment tree in the service
            # account's own (invisible) Drive rather than the team folder — silent data loss.
            # Each environment has its own subfolder of the shared "Data" folder; see
            # documentation/resources/cloud-infrastructure.md → Google Drive.
            raise RuntimeError(
                "GOOGLE_DRIVE_FOLDER_ID is not set, but Google Drive upload is enabled. "
                "Set it to this environment's Drive folder id (dev and production each have "
                "their own). The shared dev value ships in the .env from "
                "`bash scripts/fetch-env.sh`."
            )
        sem = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
        stats = {"uploaded": 0, "skipped": 0, "failed": 0}
        # Per-file mapping so callers (e.g. CamtrapDP import) can patch the media
        # record's file_path back to gdrive://<id>. Only populated when the caller
        # supplies a ``media_id`` on the file dict.
        uploaded_files: List[Dict[str, str]] = []
        total_files = len(files)
        completed_count = 0
        seen_folders: set = set()

        async def _upload_one(file_info: Dict[str, Any]) -> None:
            nonlocal completed_count
            async with sem:
                try:
                    project = file_info.get("project")
                    deployment = file_info.get("deployment")

                    if not project or not deployment:
                        logger.warning("drive_upload_skipped_no_context", filename=file_info.get("filename"))
                        completed_count += 1
                        stats["failed"] += 1
                        if file_callback:
                            await file_callback(
                                action="failed",
                                filename=file_info.get("filename", ""),
                                index=completed_count,
                                total=total_files,
                                error="no matching deployment — this camera's Deployment_ID isn't set up here (seed it, or remove these images)",
                            )
                        return

                    # 1. Ensure project folder
                    # Use preprocessed name if available, else fall back to slug
                    project_folder_name = file_info.get("_project_folder") or f"{slugify(project['name'])}_{project['id'][:8]}"
                    project_folder_id = await self.ensure_folder(
                        root_folder_id,
                        project_folder_name,
                        cache_key=f"drive:project:{project['id']}",
                    )
                    pf_key = f"project:{project['id']}"
                    if pf_key not in seen_folders:
                        seen_folders.add(pf_key)
                        if file_callback:
                            await file_callback(
                                action="folder_created",
                                folder_name=project_folder_name,
                            )

                    # 2. Ensure deployment folder (one per deployment, found by
                    # identity so re-uploads reuse it even after a rename)
                    dep_folder_name = file_info.get("_deployment_folder") or build_deployment_folder_name(
                        deployment.get("deployment_start") or deployment.get("date"),
                        deployment.get("deployment_end"),
                        deployment["id"],
                    )
                    dep_folder_id = await self.ensure_deployment_folder(
                        project_folder_id,
                        deployment["id"],
                        dep_folder_name,
                    )
                    df_key = f"deployment:{deployment['id']}"
                    if df_key not in seen_folders:
                        seen_folders.add(df_key)
                        if file_callback:
                            await file_callback(
                                action="folder_created",
                                folder_name=dep_folder_name,
                            )

                    # 3. Upload file
                    file_bytes = file_info["file_bytes"]
                    orig_name = file_info["filename"]
                    timestamp = file_info.get("timestamp")
                    # Use preprocessed filename if available, else fall back
                    drive_name = file_info.get("drive_filename") or sanitize_filename(timestamp, orig_name)

                    file_hash = compute_file_hash(file_bytes, deployment["id"])

                    file_id, was_new = await self.upload_file(
                        parent_id=dep_folder_id,
                        filename=drive_name,
                        file_bytes=file_bytes,
                        mime_type="image/jpeg",
                        file_hash=file_hash,
                    )

                    completed_count += 1
                    if file_id:
                        stats["uploaded" if was_new else "skipped"] += 1
                        # Record EVERY present file (new OR pre-existing duplicate) with its
                        # hash, so callers can register a media row — including for files that
                        # are already in Drive but have no media row yet (Guard 1 self-heal).
                        uploaded_files.append(
                            {
                                "file_id": file_id,
                                "media_id": file_info.get("media_id"),
                                "deployment_id": (file_info.get("deployment") or {}).get("id"),
                                "filename": drive_name,
                                "timestamp": file_info.get("timestamp"),
                                "file_hash": file_hash,
                                "was_new": was_new,
                                # Parsed EXIF from the upload request (may be None) —
                                # destined for media.exif_metadata.
                                "exif": file_info.get("exif"),
                            }
                        )
                        if file_callback:
                            await file_callback(
                                action="uploaded" if was_new else "skipped",
                                filename=drive_name,
                                index=completed_count,
                                total=total_files,
                            )

                except Exception as exc:
                    logger.error(
                        "drive_file_upload_error",
                        filename=file_info.get("filename"),
                        error=str(exc),
                    )
                    completed_count += 1
                    stats["failed"] += 1
                    if file_callback:
                        await file_callback(
                            action="failed",
                            filename=file_info.get("filename", ""),
                            index=completed_count,
                            total=total_files,
                            error=str(exc),
                        )

        await asyncio.gather(*[_upload_one(f) for f in files])

        logger.info("drive_batch_complete", **stats)
        # ``files`` carries the per-media Drive IDs (when media_id supplied); the
        # three integer counters keep the existing callers working unchanged.
        return {**stats, "files": uploaded_files}
