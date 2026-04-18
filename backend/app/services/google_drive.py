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
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

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

    # ── Auth ─────────────────────────────────────────────────────

    @staticmethod
    def _load_credentials() -> service_account.Credentials:
        """Load service-account credentials from file path or inline JSON."""
        raw = settings.GOOGLE_SERVICE_ACCOUNT_JSON
        if not raw:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set — cannot authenticate with Google Drive"
            )

        # Try as a file path first
        path = Path(raw)
        if path.is_file():
            info = json.loads(path.read_text(encoding="utf-8"))
        else:
            # Assume inline JSON
            info = json.loads(raw)

        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )

    # ── Redis cache helpers ──────────────────────────────────────

    @staticmethod
    async def _get_cached_folder(cache_key: str) -> Optional[str]:
        """Try to read a folder ID from Redis. Returns None on miss / error."""
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
        query = (
            f"'{parent_id}' in parents"
            f" and name = '{name}'"
            f" and mimeType = 'application/vnd.google-apps.folder'"
            f" and trashed = false"
        )
        results = (
            self._service.files()
            .list(q=query, fields="files(id)", spaces="drive", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        files = results.get("files", [])
        return files[0]["id"] if files else None

    def _create_folder(self, parent_id: str, name: str) -> str:
        """Create a new folder and return its ID."""
        metadata: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = (
            self._service.files()
            .create(body=metadata, fields="id", supportsAllDrives=True)
            .execute()
        )
        return folder["id"]

    async def ensure_folder(
        self, parent_id: str, name: str, cache_key: Optional[str] = None
    ) -> str:
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

    # ── Deduplication ────────────────────────────────────────────

    def _file_exists_by_hash(self, parent_id: str, file_hash: str) -> bool:
        """Check whether a file with the given hash already exists."""
        query = (
            f"'{parent_id}' in parents"
            f" and appProperties has {{ key='sha256' and value='{file_hash}' }}"
            f" and trashed = false"
        )
        results = (
            self._service.files()
            .list(q=query, fields="files(id)", spaces="drive", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
        )
        return len(results.get("files", [])) > 0

    # ── File upload ──────────────────────────────────────────────

    async def upload_file(
        self,
        parent_id: str,
        filename: str,
        file_bytes: bytes,
        mime_type: str,
        file_hash: str,
    ) -> Optional[str]:
        """Upload a single file to Google Drive.

        Returns the file ID on success, or ``None`` if skipped (duplicate).

        Raises on API errors (caller handles retries).
        """
        # Dedup check
        async with self._api_lock:
            exists = await asyncio.to_thread(
                self._file_exists_by_hash, parent_id, file_hash
            )
            
        if exists:
            logger.info("drive_upload_skipped_duplicate", filename=filename)
            return None

        def _do_upload() -> str:
            import requests
            import json
            import google.auth.transport.requests

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
            multipart_files = {
                'metadata': ('metadata', json.dumps(metadata), 'application/json'),
                'file': (filename, file_bytes, mime_type)
            }

            resp = requests.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id",
                headers=headers,
                files=multipart_files,
                timeout=60
            )

            if resp.status_code not in (200, 201):
                raise Exception(f"Drive Upload Error HTTP {resp.status_code}: {resp.text}")

            return resp.json()["id"]

        file_id = await asyncio.to_thread(_do_upload)
        logger.info("drive_file_uploaded", filename=filename, file_id=file_id)
        return file_id

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
        sem = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
        stats = {"uploaded": 0, "skipped": 0, "failed": 0}
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
                        stats["skipped"] += 1
                        if file_callback:
                            await file_callback(
                                action="skipped",
                                filename=file_info.get("filename", ""),
                                index=completed_count,
                                total=total_files,
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

                    # 2. Ensure deployment folder
                    # Use preprocessed name if available, else fall back to date_id
                    dep_date = deployment.get("date", "unknown-date")
                    dep_folder_name = file_info.get("_deployment_folder") or f"{dep_date}_{deployment['id'][:8]}"
                    dep_folder_id = await self.ensure_folder(
                        project_folder_id,
                        dep_folder_name,
                        cache_key=f"drive:deployment:{deployment['id']}",
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

                    file_hash = compute_file_hash(
                        file_bytes, deployment["id"]
                    )

                    result = await self.upload_file(
                        parent_id=dep_folder_id,
                        filename=drive_name,
                        file_bytes=file_bytes,
                        mime_type="image/jpeg",
                        file_hash=file_hash,
                    )

                    completed_count += 1
                    if result:
                        stats["uploaded"] += 1
                        if file_callback:
                            await file_callback(
                                action="uploaded",
                                filename=drive_name,
                                index=completed_count,
                                total=total_files,
                            )
                    else:
                        stats["skipped"] += 1
                        if file_callback:
                            await file_callback(
                                action="skipped",
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
        return stats
