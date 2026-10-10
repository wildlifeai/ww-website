"""
CamtrapDP import domain — pure business logic, no HTTP imports.

Parses a CamtrapDP v1.0 ZIP package and inserts the data into the
Wildlife Watcher database, creating a new project and placeholder
devices for each unique camera in the package.
"""

import csv
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import structlog

from app.domain.open_deployments import is_open_deployment_conflict
from app.domain.photo_preprocessing import resolve_timezone
from app.schemas.camtrapdp import CamtrapImportResult, PendingDriveUpload

logger = structlog.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# In-memory parsed representation
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CamtrapPackage:
    metadata: dict
    deployments: list[dict] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Image files physically present inside the zip: {relative_path → bytes}
    zip_files: dict[str, bytes] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────


def parse_zip(zip_bytes: bytes) -> CamtrapPackage:
    """
    Unzip a CamtrapDP package in-memory and parse all CSVs.
    Raises ValueError for invalid packages.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive.") from exc

    names = zf.namelist()

    # Locate datapackage.json (may be at root or inside a folder)
    dp_path = next((n for n in names if n.endswith("datapackage.json")), None)
    if not dp_path:
        raise ValueError("No datapackage.json found in the ZIP. Is this a CamtrapDP package?")

    metadata = json.loads(zf.read(dp_path).decode("utf-8"))

    # Determine base directory (in case files are inside a folder)
    base = dp_path.replace("datapackage.json", "")

    def read_csv(filename: str) -> list[dict]:
        path = base + filename
        if path not in names:
            # Try without subfolder
            flat = next((n for n in names if n.endswith(filename)), None)
            if not flat:
                return []
            path = flat
        content = zf.read(path).decode("utf-8-sig")  # strip BOM if present
        reader = csv.DictReader(io.StringIO(content))
        return [row for row in reader]

    pkg = CamtrapPackage(metadata=metadata)
    pkg.deployments = read_csv("deployments.csv")
    pkg.media = read_csv("media.csv")
    pkg.observations = read_csv("observations.csv")

    # Collect image/video files physically embedded in the zip
    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".avi", ".mov", ".webm"}
    for name in names:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if f".{ext}" in _IMAGE_EXTS:
            data = zf.read(name)
            pkg.zip_files[name] = data
            # Also index by basename so relative paths like 'media/foo.jpg' resolve
            pkg.zip_files[name.split("/")[-1]] = data

    if not pkg.deployments:
        pkg.warnings.append("deployments.csv is empty or missing.")

    return pkg


def validate_package(pkg: CamtrapPackage) -> list[str]:
    """Light validation — returns warnings (not errors) for missing optional fields."""
    warnings: list[str] = []

    required_dep_cols = {"deploymentID", "deploymentStart"}
    if pkg.deployments:
        missing = required_dep_cols - set(pkg.deployments[0].keys())
        if missing:
            warnings.append(f"deployments.csv is missing columns: {', '.join(missing)}")

    # Check GPS coverage
    no_gps = sum(1 for d in pkg.deployments if not d.get("latitude") or not d.get("longitude"))
    if no_gps:
        warnings.append(f"{no_gps} of {len(pkg.deployments)} deployment(s) have no GPS coordinates and will not appear on the map.")

    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────


def _str(val: Optional[str]) -> Optional[str]:
    """Convert empty CSV strings to None."""
    if val is None:
        return None
    stripped = val.strip()
    return stripped if stripped else None


def _float(val: Optional[str]) -> Optional[float]:
    try:
        return float(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


def _int(val: Optional[str]) -> Optional[int]:
    try:
        return int(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


# CamtrapDP v1.0 vocab → WW check-constraint vocab mapping
_BAIT_USE_MAP = {
    "none": "none",
    "scent": "scent",
    "food": "food",
    "visual": "visual",
    "acoustic": "acoustic",
    "other": "other",
    # CamtrapDP uses boolean-style values
    "false": "none",
    "true": "other",
}

_FEATURE_TYPE_MAP = {
    "roadTrail": "roadTrail",
    "waterSource": "waterSource",
    "burrow": "burrow",
    "nestSite": "nestSite",
    "other": "other",
    # CamtrapDP additional values → best-fit WW mapping
    "trailGame": "roadTrail",
    "trailHiking": "roadTrail",
    "road": "roadTrail",
    "culvert": "other",
    "bridge": "other",
}


def _map_bait_use(val: Optional[str]) -> Optional[str]:
    s = _str(val)
    if s is None:
        return None
    return _BAIT_USE_MAP.get(s, "other")


def _map_feature_type(val: Optional[str]) -> Optional[str]:
    s = _str(val)
    if s is None:
        return None
    return _FEATURE_TYPE_MAP.get(s, "other")


def _derive_import_provenance(cls_method: Optional[str], annotation_mode: str) -> tuple[str, str]:
    """Map a CamtrapDP observation's classificationMethod to WW provenance.

    Returns ``(source_type, review_status)`` so the annotation badge renders
    correctly instead of defaulting every imported row to a red ✕ "issue".

    - ``machine`` → AI-produced label  → ('ai', 'ai_reviewed')
    - ``human``   → human-validated    → ('human', 'human_reviewed')
    - missing/unknown:
        * annotation_mode='final'       → trust the package as validated
                                          → ('imported', 'human_reviewed')
        * annotation_mode='unprocessed' → leave for review
                                          → ('imported', 'unreviewed')
    """
    if cls_method == "machine":
        return "ai", "ai_reviewed"
    if cls_method == "human":
        return "human", "human_reviewed"
    if annotation_mode == "final":
        return "imported", "human_reviewed"
    return "imported", "unreviewed"


_OBS_TYPES = {"animal", "human", "vehicle", "blank", "unknown"}
_LIFE_STAGES = {"adult", "subadult", "juvenile", "hatchling", "unknown"}
_SEXES = {"male", "female", "unknown"}
_CLASSIFICATION_METHODS = {"human", "machine"}


def _observation_row(o: dict, ww_dep_id: str, ww_media_id: Optional[str], ww_event_id: Optional[str], annotation_mode: str) -> dict:
    """One CamtrapDP observation as an ``observations`` insert row, None values dropped.

    ``taxonID`` is not mapped: ``observations`` links a taxon through ``taxon_id`` (a ``taxa``
    uuid), and resolving a CamtrapDP taxonID to one is #135. It used to be written to a
    ``gbif_taxon_key`` column that does not exist, which failed the whole batch (#138).
    """
    obs_type = _str(o.get("observationType"))
    if obs_type and obs_type not in _OBS_TYPES:
        obs_type = "unknown"
    life_stage = _str(o.get("lifeStage"))
    if life_stage and life_stage not in _LIFE_STAGES:
        life_stage = None
    sex = _str(o.get("sex"))
    if sex and sex not in _SEXES:
        sex = None
    cls_method = _str(o.get("classificationMethod"))
    if cls_method and cls_method not in _CLASSIFICATION_METHODS:
        cls_method = None

    source_type, review_status = _derive_import_provenance(cls_method, annotation_mode)

    row = {
        "id": str(uuid.uuid4()),
        "deployment_id": ww_dep_id,
        "media_id": ww_media_id,
        "observation_event_id": ww_event_id,
        "observation_level": _str(o.get("observationLevel")) or ("media" if ww_media_id else "event"),
        "observation_type": obs_type,
        "scientific_name": _str(o.get("scientificName")),
        "source_type": source_type,
        "review_status": review_status,
        "count": _int(o.get("count")),
        "life_stage": life_stage,
        "sex": sex,
        "behavior": _str(o.get("behavior")),
        "individual_id": _str(o.get("individualID")),
        "classification_method": cls_method,
        "classified_by": _str(o.get("classifiedBy")),
        "classification_probability": _float(o.get("classificationProbability")),
        "confidence": _float(o.get("classificationProbability")),
        "observation_comments": _str(o.get("observationComments")),
        # CamtrapDP 1.0 bbox fields (camelCase) → WW schema (snake_case)
        # bboxWidth/bboxHeight in CamtrapDP ↔ bbox_w/bbox_h in our DB
        "bbox_x": _float(o.get("bboxX")),
        "bbox_y": _float(o.get("bboxY")),
        "bbox_w": _float(o.get("bboxWidth")),
        "bbox_h": _float(o.get("bboxHeight")),
    }
    return {k: v for k, v in row.items() if v is not None}


def _insert_observations(svc, batch: list[dict], warnings: list[str], what: str = "observations") -> int:
    """Insert one batch; on failure add a warning that says how many rows were lost. Returns rows inserted."""
    try:
        svc.table("observations").insert(batch).execute()
        return len(batch)
    except Exception as e:
        logger.warning("camtrapdp_import_observation_batch_failed", rows=len(batch), error=str(e))
        warnings.append(f"{len(batch)} {what} were not imported: {e}")
        return 0


def _import_device(svc, org_id: str, user_id: str, name: str) -> str:
    """The id of the organisation's placeholder device called ``name``, created if it has none.

    Its ``bluetooth_id`` is a uuid5 of the organisation and the name, so a re-import reuses the
    device and another organisation's camera with the same name gets a device of its own.
    A device made before the organisation was in the seed (name only) is still reused, but only
    by its own organisation.
    """
    scoped = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"imported-{org_id}-{name}"))
    legacy = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"imported-{name}"))
    existing = svc.table("devices").select("id").in_("bluetooth_id", [scoped, legacy]).eq("organisation_id", org_id).limit(1).execute()
    if existing.data:
        logger.info("camtrapdp_import_device_reused", name=name, device_id=existing.data[0]["id"])
        return existing.data[0]["id"]
    device_id = str(uuid.uuid4())
    svc.table("devices").insert({"id": device_id, "name": name, "organisation_id": org_id, "modified_by": user_id, "bluetooth_id": scoped}).execute()
    return device_id


def import_package(
    pkg: CamtrapPackage,
    user_id: str,
    org_id: str,
    svc,  # Supabase service-role client
    user_client=None,  # Authenticated client for trigger context
    annotation_mode: str = "final",  # 'final' = validated dataset, empty=no animals;
    #                                   'unprocessed' = no-observation media need annotating
) -> CamtrapImportResult:
    """
    Insert a parsed CamtrapDP package into the WW database.

    Strategy:
    1. Create a new project from the package title.
    2. For each unique cameraID, create a placeholder device named
       "[imported] <cameraID>" linked to the user's organisation.
    3. Insert deployments, media, and observations.

    All operations use the service-role client (RLS bypassed).
    Auth check is done in the router before calling this function.
    """
    warnings: list[str] = list(pkg.warnings)
    meta = pkg.metadata

    project_title = meta.get("title") or meta.get("name") or "Imported CamtrapDP Dataset"
    project_description = meta.get("description") or ""

    # ── 1. Create project ──────────────────────────────────────────────
    project_id = str(uuid.uuid4())

    # Use the service-role client (svc) to bypass Row Level Security.
    # The database trigger handle_new_project uses NEW.created_by to create
    # the project_admin role, so using svc is safe and avoids RLS violations.
    client_to_use = svc

    client_to_use.table("projects").insert(
        {
            "id": project_id,
            "name": project_title,
            "description": project_description,
            "organisation_id": org_id,
            "created_by": user_id,
            "modified_by": user_id,
        }
    ).execute()

    # The database trigger automatically creates the project_admin role in user_roles.

    logger.info("camtrapdp_import_project_created", project_id=project_id, title=project_title)

    # ── 2. Create placeholder devices per unique cameraID ──────────────
    camera_ids = {d.get("cameraID", "").strip() for d in pkg.deployments if d.get("cameraID", "").strip()}

    device_map: dict[str, str] = {}  # cameraID → ww device UUID

    for camera_id in camera_ids:
        try:
            device_map[camera_id] = _import_device(svc, org_id, user_id, f"[imported] {camera_id}"[:100])
        except Exception as e:
            warnings.append(f"Could not create placeholder device for camera '{camera_id}': {e}")

    logger.info("camtrapdp_import_devices_created", count=len(device_map))

    # ── 3. Insert deployments ──────────────────────────────────────────
    dep_id_map: dict[str, str] = {}  # camtrapDP deploymentID → ww UUID
    deps_inserted = 0

    for dep in pkg.deployments:
        cdp_id = dep.get("deploymentID", "").strip()
        if not cdp_id:
            warnings.append("Skipped a deployment row with no deploymentID.")
            continue

        dep_start = _str(dep.get("deploymentStart"))
        if not dep_start:
            warnings.append(f"Deployment '{cdp_id}' has no deploymentStart — skipped.")
            continue

        camera_id = dep.get("cameraID", "").strip()
        device_uuid = device_map.get(camera_id)

        if not device_uuid:
            # Create on-the-fly if missing (fallback for cameras not in the pre-scan set).
            try:
                device_uuid = _import_device(svc, org_id, user_id, f"[imported] {camera_id or 'unknown'}"[:100])
                device_map[camera_id] = device_uuid
            except Exception as e:
                warnings.append(f"Could not create device for deployment '{cdp_id}': {e}")
                continue

        # Check if a deployment already exists for this device + start time.
        # This makes re-imports idempotent: reuse the existing row's ID so that
        # subsequent media and observations can still be linked correctly.
        existing_dep = svc.table("deployments").select("id").eq("device_id", device_uuid).eq("deployment_start", dep_start).limit(1).execute()

        if existing_dep.data:
            existing_id = existing_dep.data[0]["id"]
            dep_id_map[cdp_id] = existing_id
            logger.info(
                "camtrapdp_import_deployment_reused",
                cdp_id=cdp_id,
                existing_id=existing_id,
            )
            deps_inserted += 1
            continue

        ww_id = str(uuid.uuid4())
        dep_id_map[cdp_id] = ww_id

        dep_lat = _float(dep.get("latitude"))
        dep_lon = _float(dep.get("longitude"))

        row = {
            "id": ww_id,
            "project_id": project_id,
            "device_id": device_uuid,
            "setup_by": user_id,
            "name": _str(dep.get("locationName")) or cdp_id,
            "location_name": _str(dep.get("locationName")) or cdp_id,
            "deployment_start": dep_start,
            "deployment_end": _str(dep.get("deploymentEnd")),
            "latitude": dep_lat,
            "longitude": dep_lon,
            # IANA tz for display-time local-time rendering (UTC stays the stored instant).
            "timezone": resolve_timezone(dep_lat, dep_lon),
            "accuracy": _float(dep.get("coordinateUncertainty")),
            "camera_height": _float(dep.get("cameraHeight")),
            "camera_tilt": _float(dep.get("cameraTilt")),
            "camera_model": _str(dep.get("cameraModel")),
            "bait_use": _map_bait_use(dep.get("baitUse")),
            "feature_type": _map_feature_type(dep.get("featureType")),
            "habitat": _str(dep.get("habitat")) or _str(dep.get("habitatType")),
            "detection_distance": _float(dep.get("detectionDistance")),
            "start_deployment_comments": _str(dep.get("deploymentComments")),
        }
        # Remove None values to let DB defaults apply
        row = {k: v for k, v in row.items() if v is not None}

        try:
            try:
                svc.table("deployments").insert(row).execute()
            except Exception as e:
                if not is_open_deployment_conflict(e):
                    raise
                # The camera already has an open deployment (one per camera, ww-backend #320),
                # from this package or an earlier import. Keep the data: give this deployment a
                # placeholder device of its own, which a re-import reuses, and say so.
                own_name = f"[imported] {camera_id or 'unknown'} ({cdp_id})"[:100]
                own_device = _import_device(svc, org_id, user_id, own_name)
                prior = svc.table("deployments").select("id").eq("device_id", own_device).eq("deployment_start", dep_start).limit(1).execute()
                if prior.data:
                    dep_id_map[cdp_id] = prior.data[0]["id"]
                else:
                    svc.table("deployments").insert({**row, "device_id": own_device}).execute()
                warnings.append(
                    f"Camera '{camera_id}' already has an open deployment, so deployment '{cdp_id}' (no deploymentEnd) "
                    f"was imported under its own placeholder device '{own_name}'."
                )
            deps_inserted += 1
        except Exception as e:
            # Fail-fast on first deployment error so misconfiguration is caught immediately
            raise RuntimeError(
                f"Failed to insert deployment '{cdp_id}': {e}\n"
                "Hint: check that the deployments table exists in your target project "
                "and all required columns are present."
            ) from e

    logger.info("camtrapdp_import_deployments", inserted=deps_inserted)

    # ── 4. Insert media (bulk, chunked) ──────────────────────────────────
    media_id_map: dict[str, str] = {}  # camtrapDP mediaID → ww UUID
    # Map ww_media_id → ww_dep_id for Drive upload URL backfill
    media_dep_map: dict[str, str] = {}
    media_inserted = 0
    # IDs that actually landed in the DB — only these may receive a blank
    # observation below, so a failed media batch can't FK-violate the blanks.
    inserted_media_ids: set[str] = set()
    media_batch: list[dict] = []
    pending_drive_uploads: list[PendingDriveUpload] = []
    BULK_CHUNK = 100  # PostgREST bulk insert chunk size

    for m in pkg.media:
        cdp_dep_id = m.get("deploymentID", "").strip()
        ww_dep_id = dep_id_map.get(cdp_dep_id)
        if not ww_dep_id:
            continue  # deployment was skipped

        cdp_media_id = m.get("mediaID", "").strip()
        file_path = _str(m.get("filePath")) or ""
        file_name = _str(m.get("fileName")) or file_path.split("/")[-1] or "unknown"
        mime = _str(m.get("fileMediatype")) or "image/jpeg"

        ww_id = str(uuid.uuid4())
        if cdp_media_id:
            media_id_map[cdp_media_id] = ww_id
        media_dep_map[ww_id] = ww_dep_id

        # ── Classify filePath ──────────────────────────────────────────
        # Case 1: public URL → store as-is, no upload needed
        # Case 2: relative path found in zip → queue for Google Drive upload
        # Case 3: relative path NOT in zip → store path as-is (external reference)
        stored_path = file_path  # what goes into the DB for now
        if file_path and not file_path.startswith(("http://", "https://")):
            # Look up in the zip by full relative path or basename
            zip_bytes = pkg.zip_files.get(file_path) or pkg.zip_files.get(file_path.split("/")[-1])
            if zip_bytes:
                # Resolve deployment context for Drive folder naming
                dep_row = next((d for d in pkg.deployments if dep_id_map.get((d.get("deploymentID") or "").strip()) == ww_dep_id), {})
                pending_drive_uploads.append(
                    PendingDriveUpload(
                        filename=file_name,
                        mime_type=mime,
                        file_bytes=zip_bytes,
                        media_id=ww_id,
                        deployment_id=ww_dep_id,
                        deployment_start=_str(dep_row.get("deploymentStart")),
                        deployment_end=_str(dep_row.get("deploymentEnd")),
                        location_name=_str(dep_row.get("locationName")),
                        project_id=project_id,
                        project_name=project_title,
                    )
                )
                # stored_path will be updated by the router after Drive upload
                # For now leave as the relative path; router patches it afterward

        row = {
            "id": ww_id,
            "deployment_id": ww_dep_id,
            "file_path": stored_path,
            "file_name": file_name,
            "file_mediatype": mime,
            "timestamp": _str(m.get("timestamp")),
            "file_public": file_path.startswith(("http://", "https://")),
            "favorite": m.get("favorite", "").lower() == "true",
            "media_comments": _str(m.get("mediaComments")),
        }
        row = {k: v for k, v in row.items() if v is not None}
        media_batch.append(row)

        if len(media_batch) >= BULK_CHUNK:
            try:
                svc.table("media").insert(media_batch).execute()
                media_inserted += len(media_batch)
                inserted_media_ids.update(r["id"] for r in media_batch)
            except Exception as e:
                warnings.append(f"Failed to insert media batch: {e}")
            media_batch = []

    # Flush remaining media
    if media_batch:
        try:
            svc.table("media").insert(media_batch).execute()
            media_inserted += len(media_batch)
            inserted_media_ids.update(r["id"] for r in media_batch)
        except Exception as e:
            warnings.append(f"Failed to insert media batch: {e}")

    logger.info(
        "camtrapdp_import_media",
        inserted=media_inserted,
        pending_drive_uploads=len(pending_drive_uploads),
    )

    # ── 5. Insert observations (bulk, chunked) ─────────────────────────
    #
    # observation_events must be created BEFORE observations because of
    # fk_observations_event (observation_event_id, deployment_id) →
    # observation_events (id, deployment_id).
    #
    # Strategy: one pre-pass groups observations by (eventID, dep_id),
    # derives start/end time from linked media timestamps (or falls back to
    # deployment_start), creates the event rows, then a second pass inserts
    # the observations referencing those event UUIDs.

    # Build a lookup: cdp eventID → WW observation_event UUID
    # Key: (cdp_event_id, ww_dep_id)  →  ww_event_uuid
    event_id_map: dict[tuple[str, str], str] = {}

    # Group observations by (eventID, deploymentID) to derive timestamps
    from collections import defaultdict

    event_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in pkg.observations:
        cdp_event_id = _str(o.get("eventID"))
        cdp_dep_id = o.get("deploymentID", "").strip()
        ww_dep_id = dep_id_map.get(cdp_dep_id)
        if cdp_event_id and ww_dep_id:
            event_groups[(cdp_event_id, ww_dep_id)].append(o)

    # Index media timestamps once (avoids an O(N×M) scan of pkg.media per observation).
    media_ts_map = {m.get("mediaID", "").strip(): _str(m.get("timestamp")) for m in pkg.media if m.get("mediaID")}

    # Build and insert observation_events rows
    obs_event_batch: list[dict] = []
    for (cdp_event_id, ww_dep_id), obs_in_event in event_groups.items():
        ww_event_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{cdp_event_id}-{ww_dep_id}"))
        event_id_map[(cdp_event_id, ww_dep_id)] = ww_event_uuid

        # Collect timestamps from linked media
        timestamps = []
        for o in obs_in_event:
            cdp_media_id = o.get("mediaID", "").strip()
            ww_media_id = media_id_map.get(cdp_media_id)
            if ww_media_id:
                ts = media_ts_map.get(cdp_media_id)
                if ts:
                    timestamps.append(ts)

        if timestamps:
            start_time = min(timestamps)
            end_time = max(timestamps)
        else:
            # Fall back: use deployment start as placeholder
            dep_row_start = next(
                (d.get("deploymentStart") for d in pkg.deployments if dep_id_map.get((d.get("deploymentID") or "").strip()) == ww_dep_id),
                None,
            )
            start_time = dep_row_start or "1970-01-01T00:00:00Z"
            end_time = start_time

        # Compute duration in seconds (best-effort; 0 if same time)
        try:
            from datetime import datetime

            # Try parsing with fractional seconds too
            def _parse_dt(s: str):
                # fromisoformat (3.11+) handles all ISO 8601 flavours external
                # CamtrapDP packages produce — offsets, fractional seconds, Z.
                try:
                    return datetime.fromisoformat(s.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    return None

            dt_start = _parse_dt(start_time)
            dt_end = _parse_dt(end_time)
            duration = max(0, int((dt_end - dt_start).total_seconds())) if dt_start and dt_end else 0
        except Exception:
            duration = 0

        obs_event_batch.append(
            {
                "id": ww_event_uuid,
                "deployment_id": ww_dep_id,
                "start_time": start_time,
                "end_time": end_time,
                "event_duration_seconds": duration,
                "media_count": max(1, len(obs_in_event)),
                "created_by": user_id,
            }
        )

    # Insert observation_events in chunks (reuse same BULK_CHUNK)
    for i in range(0, len(obs_event_batch), BULK_CHUNK):
        chunk = obs_event_batch[i : i + BULK_CHUNK]
        try:
            # Upsert so re-imports don't fail on duplicate event UUIDs
            svc.table("observation_events").upsert(chunk, on_conflict="id").execute()
        except Exception as e:
            warnings.append(f"Failed to insert observation_events batch: {e}")

    logger.info("camtrapdp_import_observation_events", count=len(obs_event_batch))

    obs_inserted = 0
    obs_batch: list[dict] = []
    media_with_obs: set[str] = set()  # ww media ids that received ≥1 observation

    for o in pkg.observations:
        cdp_dep_id = o.get("deploymentID", "").strip()
        ww_dep_id = dep_id_map.get(cdp_dep_id)
        if not ww_dep_id:
            continue

        cdp_media_id = o.get("mediaID", "").strip()
        ww_media_id = media_id_map.get(cdp_media_id) if cdp_media_id else None

        cdp_event_id = _str(o.get("eventID"))
        ww_event_id = event_id_map.get((cdp_event_id, ww_dep_id)) if cdp_event_id else None

        if ww_media_id:
            media_with_obs.add(ww_media_id)

        row = _observation_row(o, ww_dep_id, ww_media_id, ww_event_id, annotation_mode)
        obs_batch.append(row)

        if len(obs_batch) >= BULK_CHUNK:
            obs_inserted += _insert_observations(svc, obs_batch, warnings)
            obs_batch = []

    # Flush remaining observations
    if obs_batch:
        obs_inserted += _insert_observations(svc, obs_batch, warnings)

    # ── Synthesize "confirmed empty" rows for media with no observations ──
    # In 'final' mode the package is a finished dataset: a media item with no
    # observation means a human looked and saw no animals. Insert a reviewed
    # blank observation so the grid shows a green ✓ "Empty" rather than a red ✕.
    # In 'unprocessed' mode we leave them bare so they surface as work to do.
    if annotation_mode == "final":
        empty_batch: list[dict] = [
            {
                "id": str(uuid.uuid4()),
                "deployment_id": dep,
                "media_id": mid,
                "observation_level": "media",
                "observation_type": "blank",
                "source_type": "imported",
                "review_status": "human_reviewed",
            }
            for mid, dep in media_dep_map.items()
            if mid not in media_with_obs and mid in inserted_media_ids
        ]
        for i in range(0, len(empty_batch), BULK_CHUNK):
            obs_inserted += _insert_observations(svc, empty_batch[i : i + BULK_CHUNK], warnings, what="empty-media observations")
        if empty_batch:
            logger.info("camtrapdp_import_empty_media_marked", count=len(empty_batch))

    logger.info(
        "camtrapdp_import_complete",
        project_id=project_id,
        deployments=deps_inserted,
        media=media_inserted,
        observations=obs_inserted,
    )

    return CamtrapImportResult(
        project_id=project_id,
        project_name=project_title,
        deployments_imported=deps_inserted,
        media_imported=media_inserted,
        observations_imported=obs_inserted,
        warnings=warnings,
        pending_drive_uploads=pending_drive_uploads,
    )
