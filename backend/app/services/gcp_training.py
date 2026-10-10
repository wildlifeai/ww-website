# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Google Cloud side of native training: a GCS bucket and a Cloud Run job.

    submit()        upload runs/<run_key>/dataset/{manifest.json, images/…}, run the job
                    (backend/training/) with the run's two URIs, record the execution
                    name in runs/<run_key>/execution.json
    poll()          the execution's task counts and conditions → RunStatus
    fetch_output()  download runs/<run_key>/output/* into a directory

Every Google API call goes through ``_storage_client`` / ``_jobs_client`` /
``_executions_client`` / ``_run_job_request`` so tests can replace them. The SDKs
(``requirements-ml.txt``) are imported lazily: the lean API image never loads them.
Credentials are Application Default Credentials: the worker's runtime identity.
A retried run with the same key reuses a finished output instead of training twice.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import structlog

logger = structlog.get_logger()

# The container's contract (backend/training/README.md).
MANIFEST_NAME = "manifest.json"
ARTIFACT_MODEL_INT8 = "model_int8.tflite"
ARTIFACT_LABELS = "labels.txt"
ARTIFACT_METRICS = "metrics.json"
REQUIRED_ARTIFACTS = (ARTIFACT_MODEL_INT8, ARTIFACT_LABELS, ARTIFACT_METRICS)
EXECUTION_MARKER = "execution.json"

# Cloud Run caps GPU job tasks at 1 hour (docs: configuring/task-timeout).
CLOUD_RUN_GPU_TASK_TIMEOUT_S = 3600

TickFn = Callable[[str], Awaitable[None]]


class TrainerError(Exception):
    """The training run could not complete; the message becomes the job error."""


class TrainerNotConfigured(TrainerError):
    """A required GCP setting is missing or invalid."""


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class RunStatus:
    state: RunState
    message: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


# ── Pure helpers ──────────────────────────────────────────────────────


def dataset_prefix(run_key: str) -> str:
    return f"runs/{run_key}/dataset"


def output_prefix(run_key: str) -> str:
    return f"runs/{run_key}/output"


def marker_object(run_key: str) -> str:
    return f"runs/{run_key}/{EXECUTION_MARKER}"


def job_environment(bucket: str, run_key: str) -> Dict[str, str]:
    """The only per-run container settings; the recipe travels in the manifest."""
    return {
        "TRAINING_INPUT_URI": f"gs://{bucket}/{dataset_prefix(run_key)}",
        "TRAINING_OUTPUT_URI": f"gs://{bucket}/{output_prefix(run_key)}",
    }


def job_resource_name(project: str, region: str, job: str) -> str:
    return f"projects/{project}/locations/{region}/jobs/{job}"


def execution_state(*, succeeded: int, failed: int, cancelled: int, running: int, completed: bool, reasons: str = "") -> RunStatus:
    """Cloud Run execution counters (one task) → RunStatus."""
    detail = {"succeeded": succeeded, "failed": failed, "cancelled": cancelled, "running": running}
    suffix = f": {reasons}" if reasons else ""
    if failed or cancelled:
        return RunStatus(RunState.FAILED, f"Cloud Run task {'cancelled' if cancelled and not failed else 'failed'}{suffix}", detail)
    if succeeded:
        return RunStatus(RunState.SUCCEEDED, "Training finished", detail)
    if running:
        return RunStatus(RunState.RUNNING, "Training on Cloud Run", detail)
    if completed:
        return RunStatus(RunState.FAILED, f"Cloud Run execution completed with no successful task{suffix}", detail)
    return RunStatus(RunState.QUEUED, "Waiting for a Cloud Run instance", detail)


def _status_from_execution(execution: Any) -> RunStatus:
    """Counters and failed-condition messages off a ``run_v2.Execution`` (duck typed for tests)."""
    reasons = []
    for cond in getattr(execution, "conditions", None) or []:
        state = getattr(cond, "state", None)
        if getattr(cond, "message", "") and str(getattr(state, "name", state)).endswith("FAILED"):
            reasons.append(cond.message)
    return execution_state(
        succeeded=int(getattr(execution, "succeeded_count", 0) or 0),
        failed=int(getattr(execution, "failed_count", 0) or 0),
        cancelled=int(getattr(execution, "cancelled_count", 0) or 0),
        running=int(getattr(execution, "running_count", 0) or 0),
        completed=bool(getattr(execution, "completion_time", None)),
        reasons="; ".join(reasons),
    )


# ── SDK seams (replaced in tests) ─────────────────────────────────────


def _storage_client(project: str):
    from google.cloud import storage  # pyright: ignore[reportAttributeAccessIssue]  (requirements-ml.txt, not installed in CI)

    return storage.Client(project=project)


def _jobs_client():
    from google.cloud import run_v2

    return run_v2.JobsClient()


def _executions_client():
    from google.cloud import run_v2

    return run_v2.ExecutionsClient()


def _run_job_request(job_name: str, env: Dict[str, str], timeout_s: int):
    from google.cloud import run_v2
    from google.protobuf import duration_pb2

    container = run_v2.RunJobRequest.Overrides.ContainerOverride(env=[run_v2.EnvVar(name=k, value=v) for k, v in sorted(env.items())])
    overrides = run_v2.RunJobRequest.Overrides(container_overrides=[container], task_count=1, timeout=duration_pb2.Duration(seconds=int(timeout_s)))
    return run_v2.RunJobRequest(name=job_name, overrides=overrides)


# ── The trainer ───────────────────────────────────────────────────────


class GcpCloudRunTrainer:
    def __init__(self, *, project: str, region: str, bucket: str, job: str, run_timeout_s: int = CLOUD_RUN_GPU_TASK_TIMEOUT_S):
        missing = [
            k
            for k, v in (
                ("GOOGLE_CLOUD_PROJECT", project),
                ("CLOUD_RUN_JOB_REGION", region),
                ("GCS_TRAINING_BUCKET", bucket),
                ("TRAINING_JOB_NAME", job),
            )
            if not v
        ]
        if missing:
            raise TrainerNotConfigured(f"the gcp trainer needs {', '.join(missing)}")
        if run_timeout_s > CLOUD_RUN_GPU_TASK_TIMEOUT_S:
            raise TrainerNotConfigured(
                f"TRAINING_RUN_TIMEOUT_S {run_timeout_s} exceeds the Cloud Run GPU task limit of {CLOUD_RUN_GPU_TASK_TIMEOUT_S}s"
            )
        self.project, self.region, self.bucket_name, self.job = project, region, bucket, job
        self.run_timeout_s = int(run_timeout_s)

    @classmethod
    def from_settings(cls, settings: Any) -> "GcpCloudRunTrainer":
        return cls(
            project=settings.GOOGLE_CLOUD_PROJECT,
            region=settings.CLOUD_RUN_JOB_REGION,
            bucket=settings.GCS_TRAINING_BUCKET,
            job=settings.TRAINING_JOB_NAME,
            run_timeout_s=settings.TRAINING_RUN_TIMEOUT_S,
        )

    async def submit(self, run_key: str, objects: Sequence[Tuple[str, bytes]]) -> str:
        """Upload the dataset objects (paths relative to the dataset prefix) and start an execution."""
        if await asyncio.to_thread(self._output_complete, run_key):
            logger.info("gcp_training_reusing_output", run_key=run_key)
            return run_key
        await asyncio.to_thread(self._upload, run_key, objects)
        name, log_uri = await asyncio.to_thread(self._launch, run_key)
        await asyncio.to_thread(self._write, marker_object(run_key), json.dumps({"execution": name, "log_uri": log_uri}).encode())
        logger.info("gcp_training_submitted", run_key=run_key, execution=name, objects=len(objects), log_uri=log_uri)
        return run_key

    async def poll(self, run_key: str) -> RunStatus:
        """Safe to call repeatedly and from a restarted worker (the execution name is in GCS)."""
        if await asyncio.to_thread(self._output_complete, run_key):
            return RunStatus(RunState.SUCCEEDED, "Training output present")
        marker = await asyncio.to_thread(self._read_json, marker_object(run_key))
        if not marker or not marker.get("execution"):
            raise TrainerError(f"no execution recorded for run {run_key}")
        status = _status_from_execution(await asyncio.to_thread(self._get_execution, marker["execution"]))
        status.detail.update({"execution": marker["execution"], "log_uri": marker.get("log_uri", "")})
        return status

    async def fetch_output(self, run_key: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if not await asyncio.to_thread(self._download_prefix, output_prefix(run_key), dest):
            raise TrainerError(f"run {run_key} produced no output under gs://{self.bucket_name}/{output_prefix(run_key)}")
        return dest

    async def run(
        self,
        run_key: str,
        objects: Sequence[Tuple[str, bytes]],
        dest: Path,
        *,
        poll_interval_s: float = 15.0,
        timeout_s: float = CLOUD_RUN_GPU_TASK_TIMEOUT_S,
        on_tick: Optional[TickFn] = None,
    ) -> Path:
        """Submit, poll until terminal, download. Raises TrainerError on failure or timeout."""
        await self.submit(run_key, objects)
        started = time.monotonic()
        while True:
            status = await self.poll(run_key)
            if status.state == RunState.SUCCEEDED:
                return await self.fetch_output(run_key, dest)
            if status.state == RunState.FAILED:
                log = status.detail.get("log_uri")
                raise TrainerError(f"training run {run_key} failed: {status.message}" + (f" (logs: {log})" if log else ""))
            waited = time.monotonic() - started
            if waited >= timeout_s:
                raise TrainerError(f"training run {run_key} did not finish within {int(timeout_s)}s (last state {status.state.value})")
            if on_tick:
                await on_tick(f"{status.message} ({int(waited)}s)")
            await asyncio.sleep(poll_interval_s)

    # ── blocking helpers (run in threads) ──────────────────────────

    def _bucket(self):
        return _storage_client(self.project).bucket(self.bucket_name)

    def _output_complete(self, run_key: str) -> bool:
        bucket = self._bucket()
        return all(bucket.blob(f"{output_prefix(run_key)}/{name}").exists() for name in REQUIRED_ARTIFACTS)

    def _upload(self, run_key: str, objects: Sequence[Tuple[str, bytes]]) -> None:
        bucket = self._bucket()
        for rel, data in objects:
            content_type = "application/json" if rel.endswith(".json") else "image/jpeg"
            bucket.blob(f"{dataset_prefix(run_key)}/{rel}").upload_from_string(data, content_type=content_type)

    def _launch(self, run_key: str) -> Tuple[str, str]:
        name = job_resource_name(self.project, self.region, self.job)
        request = _run_job_request(name, job_environment(self.bucket_name, run_key), self.run_timeout_s)
        metadata = getattr(_jobs_client().run_job(request=request), "metadata", None)
        execution = getattr(metadata, "name", "") or ""
        if not execution:
            raise TrainerError("Cloud Run did not return an execution name for the training job")
        return execution, getattr(metadata, "log_uri", "") or ""

    def _write(self, name: str, data: bytes) -> None:
        self._bucket().blob(name).upload_from_string(data, content_type="application/json")

    def _read_json(self, name: str) -> Optional[Dict[str, Any]]:
        blob = self._bucket().blob(name)
        return json.loads(blob.download_as_bytes().decode("utf-8")) if blob.exists() else None

    def _get_execution(self, name: str):
        return _executions_client().get_execution(name=name)

    def _download_prefix(self, prefix: str, dest: Path) -> List[str]:
        names: List[str] = []
        for blob in self._bucket().list_blobs(prefix=prefix + "/"):
            rel = blob.name[len(prefix) + 1 :]
            if rel and not rel.endswith("/"):
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(dest / rel))
                names.append(rel)
        return names
