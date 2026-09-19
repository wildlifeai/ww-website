# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge Impulse client — request shapes and job polling, against a mock transport."""

import json

import httpx
import pytest

from app.services.edge_impulse import DSP_BLOCK_ID, LEARN_BLOCK_ID, EdgeImpulseClient, EdgeImpulseError


def _client(handler):
    return EdgeImpulseClient(
        "ei_test", 4242, transport=httpx.MockTransport(handler), studio_url="https://studio.test/v1", ingestion_url="https://ingest.test"
    )


class TestRequests:
    @pytest.mark.asyncio
    async def test_upload_sets_label_and_key_headers(self):
        seen = {}

        def handler(req: httpx.Request):
            seen["url"] = str(req.url)
            seen["label"] = req.headers.get("x-label")
            seen["key"] = req.headers.get("x-api-key")
            seen["ctype"] = req.headers.get("content-type", "")
            return httpx.Response(200, text="ok")

        n = await _client(handler).upload_samples("training", "not rat", [("a.jpg", b"1"), ("b.jpg", b"2")])
        assert n == 2
        assert seen["url"] == "https://ingest.test/api/training/files"
        assert seen["label"] == "not rat" and seen["key"] == "ei_test"
        assert seen["ctype"].startswith("multipart/form-data")

    @pytest.mark.asyncio
    async def test_upload_rejects_unknown_category(self):
        with pytest.raises(EdgeImpulseError):
            await _client(lambda r: httpx.Response(200)).upload_samples("validation", "x", [])

    @pytest.mark.asyncio
    async def test_impulse_and_training_payloads(self):
        posted = {}

        def handler(req: httpx.Request):
            posted[req.url.path] = json.loads(req.content) if req.content else None
            return httpx.Response(200, json={"success": True, "id": 77})

        c = _client(handler)
        await c.set_impulse(96)
        await c.set_dsp_config("grayscale")
        job = await c.start_training(transfer_type="transfer_mobilenetv2_a35", epochs=30, learning_rate=0.001)
        assert job == 77
        impulse = posted["/v1/api/4242/impulse"]
        assert impulse["inputBlocks"][0]["imageWidth"] == 96 and impulse["inputBlocks"][0]["resizeMode"] == "fit-short"
        assert impulse["learnBlocks"][0]["type"] == "keras-transfer-image" and impulse["learnBlocks"][0]["dsp"] == [DSP_BLOCK_ID]
        assert posted[f"/v1/api/4242/dsp/{DSP_BLOCK_ID}"] == {"config": {"channels": "Grayscale"}}
        train = posted[f"/v1/api/4242/jobs/train/keras/{LEARN_BLOCK_ID}"]
        assert train["visualLayers"] == [{"type": "transfer_mobilenetv2_a35", "neurons": 16, "dropoutRate": 0.1}]
        assert train["trainingCycles"] == 30 and train["augmentationPolicyImage"] == "all" and train["selectedModelType"] == "int8"

    @pytest.mark.asyncio
    async def test_studio_error_envelope_raises(self):
        c = _client(lambda r: httpx.Response(200, json={"success": False, "error": "No API key"}))
        with pytest.raises(EdgeImpulseError, match="No API key"):
            await c.get_project()

    @pytest.mark.asyncio
    async def test_build_and_download(self):
        calls = []

        def handler(req: httpx.Request):
            calls.append((req.method, req.url.path, dict(req.url.params)))
            if req.url.path.endswith("/deployment/download"):
                return httpx.Response(200, content=b"PK\x03\x04zip", headers={"content-type": "application/zip"})
            return httpx.Response(200, json={"success": True, "id": 9})

        c = _client(handler)
        assert await c.start_build("custom") == 9
        assert (await c.download_build("custom")).startswith(b"PK")
        assert calls[0][2] == {"type": "custom"} and calls[1][2] == {"type": "custom", "modelType": "int8", "engine": "tflite"}


class TestWaitForJob:
    @pytest.mark.asyncio
    async def test_polls_until_finished(self):
        state = {"n": 0}

        def handler(req: httpx.Request):
            state["n"] += 1
            finished = state["n"] >= 3
            return httpx.Response(200, json={"success": True, "job": {"id": 5, "finished": finished, "finishedSuccessful": True}})

        ticks = []

        async def tick(msg):
            ticks.append(msg)

        job = await _client(handler).wait_for_job(5, what="Training", timeout_s=60, poll_s=0, on_tick=tick)
        assert job["finished"] is True and state["n"] == 3 and len(ticks) == 2

    @pytest.mark.asyncio
    async def test_failed_job_raises(self):
        c = _client(lambda r: httpx.Response(200, json={"success": True, "job": {"finished": True, "finishedSuccessful": False}}))
        with pytest.raises(EdgeImpulseError, match="failed"):
            await c.wait_for_job(5, what="Training", timeout_s=60, poll_s=0)

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        c = _client(lambda r: httpx.Response(200, json={"success": True, "job": {"finished": False}}))
        with pytest.raises(EdgeImpulseError, match="did not finish"):
            await c.wait_for_job(5, what="Training", timeout_s=0, poll_s=0)
