# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge Impulse Studio + ingestion API client (the trainer behind Species Brain training).

Automates the steps of the manual recipe: clear the training-bench project, upload
labelled images, set the impulse (image input → image DSP → transfer learning),
generate features, train, build the ``custom`` int8 deployment and download it.
Studio API: https://studio.edgeimpulse.com/v1 (``x-api-key`` = project API key);
ingestion: https://ingestion.edgeimpulse.com (``/api/{category}/files``, field
``data``, ``x-label`` per request).

Only ``httpx`` is used, so this runs on the lean API image and on the worker. The
transport is injectable for tests.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
import structlog

logger = structlog.get_logger()

INPUT_BLOCK_ID = 1
DSP_BLOCK_ID = 2
LEARN_BLOCK_ID = 3
RESIZE_MODE = "fit-short"  # the guide's recommendation for centred animals


class EdgeImpulseError(Exception):
    """A Studio/ingestion call failed or a job finished unsuccessfully."""


class EdgeImpulseClient:
    def __init__(
        self,
        api_key: str,
        project_id: int,
        *,
        studio_url: str = "https://studio.edgeimpulse.com/v1",
        ingestion_url: str = "https://ingestion.edgeimpulse.com",
        timeout: float = 120.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        if not api_key or not project_id:
            raise EdgeImpulseError("Edge Impulse API key and project id are required")
        self.project_id = int(project_id)
        self._studio = studio_url.rstrip("/")
        self._ingestion = ingestion_url.rstrip("/")
        self._headers = {"x-api-key": api_key}
        self._timeout = timeout
        self._transport = transport

    # ── plumbing ───────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=self._timeout, transport=self._transport)

    def _p(self, path: str) -> str:
        return f"{self._studio}/api/{self.project_id}{path}"

    @staticmethod
    def _check(resp: httpx.Response, what: str) -> Dict[str, Any]:
        if resp.status_code >= 400:
            raise EdgeImpulseError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise EdgeImpulseError(f"{what}: non-JSON response") from exc
        if isinstance(body, dict) and body.get("success") is False:
            raise EdgeImpulseError(f"{what}: {body.get('error') or 'request failed'}")
        return body if isinstance(body, dict) else {}

    # ── project + data ─────────────────────────────────────────────

    async def get_project(self) -> Dict[str, Any]:
        async with self._client() as c:
            return self._check(await c.get(self._p("")), "get project")

    async def delete_all_samples(self) -> None:
        async with self._client() as c:
            self._check(await c.post(self._p("/raw-data/delete-all")), "clear samples")

    async def upload_samples(self, category: str, label: str, files: List[Tuple[str, bytes]], *, batch: int = 100) -> int:
        """Upload images under one label to ``training`` or ``testing``. Returns the count uploaded."""
        if category not in ("training", "testing"):
            raise EdgeImpulseError(f"unknown category {category}")
        uploaded = 0
        async with self._client() as c:
            for i in range(0, len(files), batch):
                chunk = files[i : i + batch]
                multipart = [("data", (name, data, "image/jpeg")) for name, data in chunk]
                resp = await c.post(
                    f"{self._ingestion}/api/{category}/files",
                    headers={"x-label": label, "x-disallow-duplicates": "1"},
                    files=multipart,
                )
                if resp.status_code >= 400:
                    raise EdgeImpulseError(f"upload {category}/{label}: HTTP {resp.status_code} {resp.text[:300]}")
                uploaded += len(chunk)
        return uploaded

    # ── impulse ────────────────────────────────────────────────────

    def impulse_payload(self, image_size: int) -> Dict[str, Any]:
        return {
            "inputBlocks": [
                {
                    "id": INPUT_BLOCK_ID,
                    "type": "image",
                    "name": "Image data",
                    "title": "Image data",
                    "imageWidth": image_size,
                    "imageHeight": image_size,
                    "resizeMode": RESIZE_MODE,
                    "resizeMethod": "lanczos3",
                    "cropAnchor": "middle-center",
                }
            ],
            "dspBlocks": [
                {
                    "id": DSP_BLOCK_ID,
                    "type": "image",
                    "name": "Image",
                    "axes": ["image"],
                    "title": "Image",
                    "input": INPUT_BLOCK_ID,
                    "implementationVersion": 1,
                }
            ],
            "learnBlocks": [
                {
                    "id": LEARN_BLOCK_ID,
                    "type": "keras-transfer-image",
                    "name": "Transfer learning",
                    "dsp": [DSP_BLOCK_ID],
                    "title": "Transfer learning (Images)",
                }
            ],
        }

    async def set_impulse(self, image_size: int) -> None:
        async with self._client() as c:
            self._check(await c.post(self._p("/impulse"), json=self.impulse_payload(image_size)), "set impulse")

    async def set_dsp_config(self, colour: str) -> None:
        channels = "Grayscale" if colour == "grayscale" else "RGB"
        async with self._client() as c:
            self._check(await c.post(self._p(f"/dsp/{DSP_BLOCK_ID}"), json={"config": {"channels": channels}}), "set DSP config")

    # ── jobs ───────────────────────────────────────────────────────

    async def start_generate_features(self) -> int:
        async with self._client() as c:
            body = self._check(
                await c.post(
                    self._p("/jobs/generate-features"),
                    json={"dspId": DSP_BLOCK_ID, "calculateFeatureImportance": False, "skipFeatureExplorer": True},
                ),
                "generate features",
            )
        return int(body["id"])

    def training_payload(self, *, transfer_type: str, epochs: int, learning_rate: float, augmentation: bool = True) -> Dict[str, Any]:
        return {
            "mode": "visual",
            "visualLayers": [{"type": transfer_type, "neurons": 16, "dropoutRate": 0.1}],
            "trainingCycles": epochs,
            "learningRate": learning_rate,
            "augmentationPolicyImage": "all" if augmentation else "none",
            "trainTestSplit": 0.2,
            "autoClassWeights": True,
            "selectedModelType": "int8",
            "profileInt8": True,
        }

    async def start_training(self, *, transfer_type: str, epochs: int, learning_rate: float, augmentation: bool = True) -> int:
        payload = self.training_payload(transfer_type=transfer_type, epochs=epochs, learning_rate=learning_rate, augmentation=augmentation)
        async with self._client() as c:
            body = self._check(await c.post(self._p(f"/jobs/train/keras/{LEARN_BLOCK_ID}"), json=payload), "start training")
        return int(body["id"])

    async def get_job(self, job_id: int) -> Dict[str, Any]:
        async with self._client() as c:
            body = self._check(await c.get(self._p(f"/jobs/{job_id}/status")), f"job {job_id} status")
        return body.get("job") or {}

    async def wait_for_job(
        self,
        job_id: int,
        *,
        what: str,
        timeout_s: int,
        poll_s: float = 5.0,
        on_tick: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Poll until the Studio job finishes; raise if it fails or times out."""
        waited = 0.0
        while True:
            job = await self.get_job(job_id)
            if job.get("finished"):
                if job.get("finishedSuccessful") is False:
                    raise EdgeImpulseError(f"{what} failed in Edge Impulse (job {job_id})")
                return job
            if waited >= timeout_s:
                raise EdgeImpulseError(f"{what} did not finish within {timeout_s}s (job {job_id})")
            if on_tick:
                await on_tick(f"{what}… ({int(waited)}s)")
            await asyncio.sleep(poll_s)
            waited += poll_s

    # ── results ────────────────────────────────────────────────────

    async def get_training_metrics(self) -> Dict[str, Any]:
        """Validation metrics of the trained block (accuracy, confusion matrix per model type)."""
        async with self._client() as c:
            body = self._check(await c.get(self._p(f"/training/keras/{LEARN_BLOCK_ID}/metadata")), "training metadata")
        metrics = body.get("modelValidationMetrics") or []
        picked = next((m for m in metrics if m.get("type") == "int8"), metrics[0] if metrics else {})
        return {
            "class_names": body.get("classNames") or [],
            "accuracy": picked.get("accuracy"),
            "loss": picked.get("loss"),
            "confusion_matrix": picked.get("confusionMatrix"),
            "confusion_matrix_values": picked.get("confusionMatrixValues"),
            "model_type": picked.get("type"),
        }

    async def list_deployment_formats(self) -> List[str]:
        async with self._client() as c:
            body = self._check(await c.get(self._p("/deployment/targets")), "deployment targets")
        return [t.get("format") for t in body.get("targets") or [] if t.get("format")]

    async def start_build(self, deploy_format: str, *, engine: str = "tflite", model_type: str = "int8") -> int:
        async with self._client() as c:
            body = self._check(
                await c.post(self._p("/jobs/build-ondevice-model"), params={"type": deploy_format}, json={"engine": engine, "modelType": model_type}),
                "build deployment",
            )
        return int(body["id"])

    async def download_build(self, deploy_format: str, *, engine: str = "tflite", model_type: str = "int8") -> bytes:
        async with self._client() as c:
            resp = await c.get(self._p("/deployment/download"), params={"type": deploy_format, "modelType": model_type, "engine": engine})
        if resp.status_code >= 400:
            raise EdgeImpulseError(f"download deployment: HTTP {resp.status_code} {resp.text[:300]}")
        if not resp.content:
            raise EdgeImpulseError("download deployment: empty response")
        return resp.content
