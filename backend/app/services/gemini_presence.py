# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini animal-presence filter (infrastructure layer, Cloud AI).

Asks a Gemini model whether a camera-trap frame contains an animal and returns
a strict-JSON verdict with token usage and USD cost attached. Three
token-saving variants are implemented so the evaluation script can price them
against each other on the same labelled frames:

- ``single``:        one frame per call, downscaled so it bills as a single tile
                     (and requested at ``media_resolution`` low on Gemini 3).
- ``contact_sheet``: the frames of one trigger burst stitched into one labelled
                     grid, one call per burst, one verdict per cell.
- ``batch``:         ``single`` images submitted through the asynchronous Batch
                     API at the documented 50% discount.

Verified against the official docs on 2026-09-26 (see ``gemini_pricing`` for the
token rules and prices):

- Model ids:        https://ai.google.dev/gemini-api/docs/models
- Image input:      https://ai.google.dev/gemini-api/docs/image-understanding
                    (bounding boxes come back as ``[ymin, xmin, ymax, xmax]``
                    "normalized to 0-1000")
- JSON output:      https://ai.google.dev/gemini-api/docs/structured-output
- Batch API:        https://ai.google.dev/gemini-api/docs/batch-mode
- SDK:              https://googleapis.github.io/python-genai/ (google-genai 2.25.0,
                    ``genai.Client(api_key=...)``, ``client.models.generate_content``,
                    ``types.GenerateContentConfig(response_mime_type, response_json_schema,
                    media_resolution)``, ``types.Part.from_bytes``, ``client.batches``)

Design: the SDK is imported lazily inside the three network functions
(``_generate_content``, ``_batch_create``, ``_batch_get``) so this module loads
without the package and tests monkeypatch those three names; everything else
(image preparation, prompt, schema, parsing, cost) is pure.

Prompt versions: ``PROMPT_VERSION`` (v1) is the default everywhere, the
pipeline included; v2 (structured evidence) and v3 (animals only, a separate
``has_person``, short answers) stay selectable so the eval can compare them.
See the ``PROMPT_VERSION`` block below.
"""

from __future__ import annotations

import io
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import structlog
from PIL import Image, ImageDraw, ImageFont

from app.services.gemini_pricing import (
    ASSUMED_OUTPUT_TOKENS,
    cost_usd,
    estimate_image_tokens,
    estimate_text_tokens,
)

logger = structlog.get_logger()

VARIANTS = ("single", "contact_sheet", "batch")

# Variant (a): the longest side after downscaling. 768 is one tile under the
# tile rule; WW500 frames (640x480) are untouched.
SINGLE_MAX_PX = 768
# Variant (b): grid geometry. 4:3 cells at 380x285 keep a rat at roughly 60% of
# the size it has on a 640x480 frame, and two cells plus the gutter (764 px) stay
# inside one 768 px tile so a two-frame burst bills as a single tile under the
# tile rule; three columns (1148 px) are two tiles for up to six frames.
SHEET_COLS = 3
SHEET_CELL_SIZE = (380, 285)
SHEET_MAX_CELLS = 6
SHEET_GUTTER = 4
# Gemini 3 per-image token allocation requested for every variant.
DEFAULT_MEDIA_RESOLUTION = "low"

# Quota handling for the online endpoint. The free tier allows 15 requests per
# minute per model and answers a 429 RESOURCE_EXHAUSTED carrying "retry in Ns";
# we honour that delay (or a default) a bounded number of times, so an eval on
# the free tier just runs slowly instead of dying.
QUOTA_RETRY_ATTEMPTS = 8
QUOTA_DEFAULT_WAIT_S = 30.0
_RETRY_IN_RE = re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE)

# Batch API polling.
BATCH_POLL_SECONDS = 30.0
BATCH_MAX_WAIT_SECONDS = 24 * 3600.0
_BATCH_DONE_STATES = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED", "JOB_STATE_PARTIALLY_SUCCEEDED"}

# ── Prompt versions ──────────────────────────────────────────────────
# v1 asked for has_animal + confidence + description + bbox. The confidence came
# back as 1.0 on most answers (report section 6.6), so it is not a calibrated
# probability and is excluded from every decision. v2 drops it from the schema
# and asks for structured evidence instead (visibility, size, location, visual
# conditions, evidence strings), which the fusion step scores with hand-set
# weights (domain/burst_evidence.py). The version travels on every verdict and
# in the eval cache key, so answers from different prompts are never mixed.
#
# v1 is the default (2026-10-09). On the 632 frames of the labelled set both
# answered, v2 matched v1 on wildlife (98.8% vs 98.1% recall) at about $0.35
# vs $0.21 per 1,000 frames (726 vs 473 tokens per frame) and called 68 of 82
# person frames an animal while its own description named the human. v3 asks
# only for what the fusion reads (has_animal, visibility, bbox), plus
# has_person, since a person is never an animal; its answer is v1's length
# (48 to 76 output tokens) and its longer prompt puts it at about $0.22 per
# 1,000 frames. It is opt-in until its person frames are measured. Never edit a released prompt's text: the eval cache is keyed
# by version, not text, so a changed prompt must be a new version.
PROMPT_VERSION = "v1"
PROMPT_VERSIONS = ("v1", "v2", "v3")

# The v2 vocabulary (architecture report section 9). Every string is enumerated
# in the JSON schema, so a stray label is a schema violation and never a silent
# new category.
VISIBILITY_VALUES = ("clear", "partial", "obscured", "none")
SIZE_VALUES = ("tiny", "small", "medium", "large", "none")
LOCATION_VALUES = ("centre", "edge", "corner", "none")
VISUAL_CONDITIONS = (
    "night_ir",
    "low_light",
    "motion_blur",
    "rain",
    "fog",
    "lens_obstruction",
    "overexposed",
    "underexposed",
    "vegetation",
    "none",
)
EVIDENCE_VALUES = ("eye_shine", "body_outline", "fur_texture", "limb", "tail", "head", "motion_trail", "shadow", "none")

# media_evidence values for the labels (report section 8): gemini_visibility and gemini_size.
VISIBILITY_VALUE = {"clear": 1.0, "partial": 0.66, "obscured": 0.33, "none": 0.0}
SIZE_VALUE = {"tiny": 0.25, "small": 0.5, "medium": 0.75, "large": 1.0, "none": 0.0}
# What the Gemini observation row's ``confidence`` is made of (report section 9):
# the visibility weight plus 0.05 per evidence item (up to three), clamped. It is
# displayed and stored, and never used by the fusion score.
VISIBILITY_WEIGHT = {"clear": 0.85, "partial": 0.70, "obscured": 0.50, "none": 0.0}
EVIDENCE_ITEM_WEIGHT = 0.05
EVIDENCE_ITEMS_COUNTED = 3

_PROMPT_SINGLE_V1 = (
    "Examine this camera trap image. It may have been automatically flagged as empty. "
    "Inspect foliage, shadows, ground textures and the frame borders for subtle wildlife evidence: "
    "reflective eye shine, a partial tail, snout, ear or leg, camouflaged fur or feathers, or motion blur. "
    "Insects and spiders on the lens do not count as animals. "
    "Return JSON with fields: has_animal (bool), confidence (0.0-1.0), description (str, one sentence), "
    "and bbox as [ymin, xmin, ymax, xmax] normalised to 0-1000 when an animal is visible, else null."
)

_PROMPT_SHEET_V1 = (
    "This image is a contact sheet of {n} camera trap frames from one trigger, laid out in a grid. "
    "Each cell has its index number burned into its top-left corner (1 to {n}), read left to right, top to bottom. "
    "Judge every cell independently. Some frames may have been automatically flagged as empty: inspect foliage, shadows, "
    "ground textures and cell borders for subtle wildlife evidence such as reflective eye shine, a partial tail, snout, ear or leg, "
    "camouflaged fur or feathers, or motion blur. Insects and spiders on the lens do not count as animals. "
    "Return JSON with a field cells: a list with exactly one entry per cell, each entry having index (int, the burned-in number), "
    "has_animal (bool), confidence (0.0-1.0), description (str, one sentence), and bbox as [ymin, xmin, ymax, xmax] normalised to 0-1000 "
    "of the WHOLE contact sheet when an animal is visible in that cell, else null."
)

# Report section 9, verbatim apart from the contact-sheet framing.
_CUES_V2 = (
    "Inspect foliage, shadows, ground textures and the frame borders for wildlife evidence: reflective eye shine, "
    "a body outline, fur or feather texture, a partial limb, tail, ear or snout, or a motion-blur trail. "
    "Small rodents very close to the lens on night IR frames can appear as a dark low-contrast shape with no eye shine "
    "and no clear outline. Insects and spiders on the lens do not count. "
)

_FIELDS_V2 = (
    "has_animal: bool; "
    'animal_visibility: "clear" | "partial" | "obscured" | "none"; '
    'animal_size: "tiny" | "small" | "medium" | "large" | "none" (share of the frame: under 2%, 2 to 10%, 10 to 30%, over 30%); '
    'animal_location: "centre" | "edge" | "corner" | "none"; '
    'visual_conditions: list of "night_ir", "low_light", "motion_blur", "rain", "fog", "lens_obstruction", "overexposed", '
    '"underexposed", "vegetation", "none"; '
    'evidence: list of "eye_shine", "body_outline", "fur_texture", "limb", "tail", "head", "motion_trail", "shadow", "none"; '
    "description: one sentence; "
)

_PROMPT_SINGLE_V2 = (
    "Examine this camera trap image. It may have been automatically flagged as empty. "
    + _CUES_V2
    + "Return JSON: "
    + _FIELDS_V2
    + "bbox: [ymin, xmin, ymax, xmax] normalised to 0-1000 when an animal is visible, else null."
)

_PROMPT_SHEET_V2 = (
    "This image is a contact sheet of {n} camera trap frames from one trigger, laid out in a grid. "
    "Each cell has its index number burned into its top-left corner (1 to {n}), read left to right, top to bottom. "
    "Judge every cell independently. Some may have been automatically flagged as empty. "
    + _CUES_V2
    + "Return JSON with a field cells: a list with exactly one entry per cell, each entry having index: int (the burned-in number); "
    + _FIELDS_V2
    + "bbox: [ymin, xmin, ymax, xmax] normalised to 0-1000 of the WHOLE contact sheet when an animal is visible in that cell, else null."
)

# v3: animals only, people reported on their own, and a short answer. The cue
# list is v2's in fewer words; has_person comes first in the schema so the
# model settles "is this a person" before it answers has_animal.
_CUES_V3 = (
    "Look for subtle wildlife evidence: eye shine, a body outline, fur or feathers, a partial limb, tail or snout, "
    "or a motion-blur trail; a small rodent close to the lens on a night IR frame can be a dark low-contrast shape. "
    "A person is never an animal: a human or any part of one (hand, arm, leg, face) sets has_person, not has_animal. "
    "Insects and spiders on the lens do not count. "
)

_FIELDS_V3 = 'has_person: bool; has_animal: bool; animal_visibility: "clear" | "partial" | "obscured" | "none"; description: at most 8 words; '

_PROMPT_SINGLE_V3 = (
    "Examine this camera trap image for animals. It may have been automatically flagged as empty. "
    + _CUES_V3
    + "Return JSON: "
    + _FIELDS_V3
    + "bbox: [ymin, xmin, ymax, xmax] normalised to 0-1000 around the animal, else null."
)

_PROMPT_SHEET_V3 = (
    "This image is a contact sheet of {n} camera trap frames from one trigger, laid out in a grid. "
    "Each cell has its index number burned into its top-left corner (1 to {n}), read left to right, top to bottom. "
    "Judge every cell independently for animals. Some may have been automatically flagged as empty. "
    + _CUES_V3
    + "Return JSON with a field cells: one entry per cell, each with index: int (the burned-in number); "
    + _FIELDS_V3
    + "bbox: [ymin, xmin, ymax, xmax] normalised to 0-1000 of the WHOLE contact sheet around the animal in that cell, else null."
)

_PROMPTS: dict[str, tuple[str, str]] = {
    "v1": (_PROMPT_SINGLE_V1, _PROMPT_SHEET_V1),
    "v2": (_PROMPT_SINGLE_V2, _PROMPT_SHEET_V2),
    "v3": (_PROMPT_SINGLE_V3, _PROMPT_SHEET_V3),
}

_BBOX_SCHEMA = {"anyOf": [{"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, {"type": "null"}]}

_VERDICT_PROPERTIES_V1: dict[str, Any] = {
    "has_animal": {"type": "boolean"},
    "confidence": {"type": "number"},
    "description": {"type": "string"},
    "bbox": _BBOX_SCHEMA,
}
_VERDICT_REQUIRED_V1 = ["has_animal", "confidence", "description"]

_VERDICT_PROPERTIES_V2: dict[str, Any] = {
    "has_animal": {"type": "boolean"},
    "animal_visibility": {"type": "string", "enum": list(VISIBILITY_VALUES)},
    "animal_size": {"type": "string", "enum": list(SIZE_VALUES)},
    "animal_location": {"type": "string", "enum": list(LOCATION_VALUES)},
    "visual_conditions": {"type": "array", "items": {"type": "string", "enum": list(VISUAL_CONDITIONS)}},
    "evidence": {"type": "array", "items": {"type": "string", "enum": list(EVIDENCE_VALUES)}},
    "description": {"type": "string"},
    "bbox": _BBOX_SCHEMA,
}
_VERDICT_REQUIRED_V2 = ["has_animal", "animal_visibility", "animal_size", "animal_location", "visual_conditions", "evidence", "description", "bbox"]

_VERDICT_PROPERTIES_V3: dict[str, Any] = {
    "has_person": {"type": "boolean"},
    "has_animal": {"type": "boolean"},
    "animal_visibility": {"type": "string", "enum": list(VISIBILITY_VALUES)},
    "description": {"type": "string"},
    "bbox": _BBOX_SCHEMA,
}
_VERDICT_REQUIRED_V3 = ["has_person", "has_animal", "animal_visibility", "description", "bbox"]

_SCHEMAS: dict[str, tuple[dict[str, Any], list[str]]] = {
    "v1": (_VERDICT_PROPERTIES_V1, _VERDICT_REQUIRED_V1),
    "v2": (_VERDICT_PROPERTIES_V2, _VERDICT_REQUIRED_V2),
    "v3": (_VERDICT_PROPERTIES_V3, _VERDICT_REQUIRED_V3),
}


def _check_prompt_version(prompt_version: str) -> None:
    if prompt_version not in PROMPT_VERSIONS:
        raise ValueError(f"unknown prompt version {prompt_version!r}; expected one of {PROMPT_VERSIONS}")


class PresenceParseError(ValueError):
    """The model's text was not the JSON verdict we asked for."""


# ── Result types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0  # candidates (the visible answer)
    thought_tokens: int = 0  # billed as output on thinking models
    total_tokens: int = 0

    @property
    def billed_output_tokens(self) -> int:
        return self.output_tokens + self.thought_tokens

    @classmethod
    def from_usage_metadata(cls, usage: Any) -> "TokenUsage":
        """Read the SDK's ``response.usage_metadata`` (attributes or a dict); missing counts read as 0."""

        def _get(name: str) -> int:
            val = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            return int(val or 0)

        if usage is None:
            return cls()
        return cls(
            input_tokens=_get("prompt_token_count"),
            output_tokens=_get("candidates_token_count"),
            thought_tokens=_get("thoughts_token_count"),
            total_tokens=_get("total_token_count"),
        )


@dataclass(frozen=True)
class PresenceVerdict:
    """One frame's verdict. ``bbox`` is (x, y, w, h) normalised 0-1 to THAT frame, or None.

    ``confidence`` is the model's own number on a v1 answer and, on a v2 answer,
    the value :func:`derived_confidence` computes from visibility and evidence
    (the schema no longer asks the model for one). Either way it is displayed
    and stored, never used by the fusion score. The structured fields are the
    v2 evidence, each one of its fixed vocabulary (``none`` when absent):
    ``animal_visibility`` in ``VISIBILITY_VALUES``, ``animal_size`` in
    ``SIZE_VALUES``, ``animal_location`` in ``LOCATION_VALUES``,
    ``visual_conditions`` and ``evidence`` from their lists. A v3 answer fills
    ``animal_visibility`` only, plus ``has_person`` (None on v1 and v2, which
    do not ask).
    """

    has_animal: bool
    confidence: Optional[float] = None
    description: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None
    animal_visibility: str = "none"
    animal_size: str = "none"
    animal_location: str = "none"
    visual_conditions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION
    has_person: Optional[bool] = None

    @property
    def evidence_items(self) -> tuple[str, ...]:
        """The evidence labels other than ``none``."""
        return tuple(e for e in self.evidence if e != "none")


def derived_confidence(visibility: str, evidence: Sequence[str]) -> float:
    """``observations.confidence`` for a v2 Gemini row: visibility weight + 0.05 per evidence item (max 3), clamped."""
    items = [e for e in evidence if e != "none"]
    raw = VISIBILITY_WEIGHT.get(visibility, 0.0) + EVIDENCE_ITEM_WEIGHT * min(EVIDENCE_ITEMS_COUNTED, len(items))
    return round(min(1.0, max(0.0, raw)), 4)


@dataclass
class PresenceResult:
    """Outcome of one API call over one or more frames (verdicts align with the input frames)."""

    model: str
    variant: str
    verdicts: list[Optional[PresenceVerdict]]
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    raw_text: str = ""
    error: Optional[str] = None
    prompt_version: str = PROMPT_VERSION

    @property
    def frames(self) -> int:
        return len(self.verdicts)


@dataclass(frozen=True)
class PreparedImage:
    """JPEG bytes ready to send plus, per input frame, its pixel box inside the image."""

    data: bytes
    width: int
    height: int
    cell_boxes: tuple[tuple[int, int, int, int], ...]  # (x0, y0, x1, y1) per input frame
    mime_type: str = "image/jpeg"


# ── Image preparation (pure) ─────────────────────────────────────────


def _open_rgb(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _encode_jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def prepare_single(data: bytes, max_px: int = SINGLE_MAX_PX) -> PreparedImage:
    """Variant (a): downscale so the longest side is at most ``max_px`` (one tile), keep aspect."""
    img = _open_rgb(data)
    w, h = img.size
    scale = min(1.0, max_px / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    w, h = img.size
    return PreparedImage(data=_encode_jpeg(img), width=w, height=h, cell_boxes=((0, 0, w, h),))


def sheet_layout(n: int, cols: int = SHEET_COLS) -> tuple[int, int]:
    """(cols, rows) for ``n`` cells: never wider than ``cols``, never more rows than needed."""
    if n < 1:
        raise ValueError("a contact sheet needs at least one frame")
    c = min(n, cols)
    return c, math.ceil(n / c)


def _label_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:  # no TrueType font on this host; Pillow's bitmap font still works
        return ImageFont.load_default()


def prepare_contact_sheet(
    frames: Sequence[bytes],
    cols: int = SHEET_COLS,
    cell_size: tuple[int, int] = SHEET_CELL_SIZE,
    gutter: int = SHEET_GUTTER,
) -> PreparedImage:
    """Variant (b): stitch a burst into one grid with 1-based index labels burned in.

    Each frame is fitted into a ``cell_size`` cell (aspect kept, letterboxed on mid
    grey), and its label is drawn in the top-left corner on a black box so the model
    can answer per cell. ``cell_boxes`` records where each frame landed so a sheet
    bbox can be mapped back to the frame (:func:`map_sheet_bbox_to_frame`).
    """
    if not frames:
        raise ValueError("a contact sheet needs at least one frame")
    if len(frames) > SHEET_MAX_CELLS:
        raise ValueError(f"at most {SHEET_MAX_CELLS} frames per contact sheet, got {len(frames)}")
    c, r = sheet_layout(len(frames), cols)
    cw, ch = cell_size
    sheet_w = c * cw + (c - 1) * gutter
    sheet_h = r * ch + (r - 1) * gutter
    sheet = Image.new("RGB", (sheet_w, sheet_h), (128, 128, 128))
    draw = ImageDraw.Draw(sheet)
    font = _label_font(max(14, ch // 10))
    boxes: list[tuple[int, int, int, int]] = []
    for idx, data in enumerate(frames):
        col, row = idx % c, idx // c
        x0, y0 = col * (cw + gutter), row * (ch + gutter)
        img = _open_rgb(data)
        img.thumbnail((cw, ch), Image.LANCZOS)
        ox, oy = x0 + (cw - img.width) // 2, y0 + (ch - img.height) // 2
        sheet.paste(img, (ox, oy))
        boxes.append((ox, oy, ox + img.width, oy + img.height))
        label = str(idx + 1)
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        draw.rectangle((ox, oy, ox + tw + 10, oy + th + 8), fill=(0, 0, 0))
        draw.text((ox + 5, oy + 2), label, fill=(255, 255, 0), font=font)
    return PreparedImage(data=_encode_jpeg(sheet), width=sheet_w, height=sheet_h, cell_boxes=tuple(boxes))


def bbox_1000_to_xywh(raw: Any) -> Optional[tuple[float, float, float, float]]:
    """``[ymin, xmin, ymax, xmax]`` on 0-1000 -> (x, y, w, h) on 0-1, or None when unusable."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (min(1000.0, max(0.0, float(v))) for v in raw)
    except (TypeError, ValueError):
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    return (xmin / 1000, ymin / 1000, (xmax - xmin) / 1000, (ymax - ymin) / 1000)


def map_sheet_bbox_to_frame(raw: Any, sheet: PreparedImage, cell_index: int) -> Optional[tuple[float, float, float, float]]:
    """Map a bbox given on the WHOLE sheet (0-1000) to (x, y, w, h) on 0-1 of frame ``cell_index``.

    The box is clipped to the frame's cell; None when it does not overlap the cell.
    """
    whole = bbox_1000_to_xywh(raw)
    if whole is None:
        return None
    x, y, w, h = whole
    px0, py0, px1, py1 = x * sheet.width, y * sheet.height, (x + w) * sheet.width, (y + h) * sheet.height
    cx0, cy0, cx1, cy1 = sheet.cell_boxes[cell_index]
    ix0, iy0, ix1, iy1 = max(px0, cx0), max(py0, cy0), min(px1, cx1), min(py1, cy1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    cw, chh = cx1 - cx0, cy1 - cy0
    return ((ix0 - cx0) / cw, (iy0 - cy0) / chh, (ix1 - ix0) / cw, (iy1 - iy0) / chh)


# ── Prompt, schema, parsing (pure) ───────────────────────────────────


def build_prompt(variant: str, n_frames: int = 1, prompt_version: str = PROMPT_VERSION) -> str:
    _check_prompt_version(prompt_version)
    single, sheet = _PROMPTS[prompt_version]
    if variant == "contact_sheet":
        return sheet.format(n=n_frames)
    return single


def response_json_schema(variant: str, prompt_version: str = PROMPT_VERSION) -> dict[str, Any]:
    """JSON Schema handed to the SDK's ``response_json_schema`` (strict JSON mode).

    The contact sheet returns one verdict object per cell (plus its ``index``);
    ``confidence`` exists only in the v1 schema, ``has_person`` only in v3.
    """
    _check_prompt_version(prompt_version)
    props, required = _SCHEMAS[prompt_version]
    if variant == "contact_sheet":
        cell = {"type": "object", "properties": {"index": {"type": "integer"}, **props}, "required": ["index", *required]}
        return {"type": "object", "properties": {"cells": {"type": "array", "items": cell}}, "required": ["cells"]}
    return {"type": "object", "properties": props, "required": required}


def format_verdict_comment(verdict: PresenceVerdict) -> str:
    """Compact, parseable ``observation_comments`` text for a v2 verdict.

    ``visibility=partial; size=small; location=edge; conditions=night_ir,low_light; evidence=tail,fur_texture | <description>``.
    Until the ``media_evidence`` table exists this is where the structured fields live;
    :func:`parse_verdict_comment` reads them back. ``none`` values and empty lists are left out.
    A v3 answer that saw a person adds ``person=yes``.
    """
    parts = [f"visibility={verdict.animal_visibility}"]
    if verdict.has_person:
        parts.append("person=yes")
    if verdict.animal_size and verdict.animal_size != "none":
        parts.append(f"size={verdict.animal_size}")
    if verdict.animal_location and verdict.animal_location != "none":
        parts.append(f"location={verdict.animal_location}")
    conditions = [c for c in verdict.visual_conditions if c != "none"]
    if conditions:
        parts.append("conditions=" + ",".join(conditions))
    if verdict.evidence_items:
        parts.append("evidence=" + ",".join(verdict.evidence_items))
    head = "; ".join(parts)
    return f"{head} | {verdict.description}" if verdict.description else head


_COMMENT_LIST_KEYS = ("conditions", "evidence")
_COMMENT_KEYS = ("visibility", "person", "size", "location", *_COMMENT_LIST_KEYS)


def parse_verdict_comment(text: Optional[str]) -> dict[str, Any]:
    """Inverse of :func:`format_verdict_comment`; a plain v1 description yields ``{}``.

    Returns the keys found among ``visibility``, ``person``, ``size``, ``location`` (strings),
    ``conditions`` and ``evidence`` (lists), plus ``description``.
    """
    if not text:
        return {}
    head, _, description = text.partition(" | ")
    out: dict[str, Any] = {}
    for chunk in head.split(";"):
        key, sep, value = chunk.strip().partition("=")
        if not sep or key not in _COMMENT_KEYS:
            continue
        out[key] = [c for c in value.split(",") if c] if key in _COMMENT_LIST_KEYS else value.strip()
    if not out:
        return {}
    out["description"] = description.strip()
    return out


def _load_json(text: str) -> Any:
    """Parse the model text, tolerating a ``json`` code fence around it."""
    if not text or not text.strip():
        raise PresenceParseError("empty response")
    body = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PresenceParseError(f"not JSON: {exc.msg} at {exc.pos}") from exc


def _enum_or_none(value: Any, allowed: Sequence[str]) -> Optional[str]:
    v = str(value).strip().lower() if isinstance(value, str) else None
    return v if v in allowed else None


def _string_list(value: Any, allowed: Optional[Sequence[str]] = None, limit: int = 12) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if allowed is not None:
            s = s.lower()
            if s not in allowed:
                continue
        if s and s not in out:
            out.append(s)
    return tuple(out[:limit])


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _verdict_from_obj(obj: Any, bbox_mapper, prompt_version: str = PROMPT_VERSION) -> PresenceVerdict:
    """A verdict object (either prompt version) -> PresenceVerdict, tolerant of type slips.

    v2 rules: an empty frame is recorded with visibility, size and location
    ``none`` whatever else the model said. An animal answer whose visibility is
    missing or outside the vocabulary is a parse error (the schema enumerates
    it; a stray label must never become a silent category), while an unknown
    size or location falls back to ``none`` and unknown list items are dropped,
    because those only feed strata. ``confidence`` on a v2 or v3 answer is
    :func:`derived_confidence`; on v1 it is the model's number. ``has_person``
    is read on v3 only (False when the model left it out).
    """
    if not isinstance(obj, dict) or "has_animal" not in obj:
        raise PresenceParseError("verdict object missing has_animal")
    has_animal = _as_bool(obj["has_animal"])
    has_person = _as_bool(obj.get("has_person")) if prompt_version == "v3" else None
    conditions = _string_list(obj.get("visual_conditions"), VISUAL_CONDITIONS)
    evidence = _string_list(obj.get("evidence"), EVIDENCE_VALUES)
    if not has_animal:
        visibility, size, location = "none", "none", "none"
        evidence = tuple(e for e in evidence if e == "none")
    else:
        visibility = _enum_or_none(obj.get("animal_visibility"), VISIBILITY_VALUES)
        if prompt_version != "v1" and visibility in (None, "none"):
            raise PresenceParseError(f"animal answer with animal_visibility {obj.get('animal_visibility')!r}")
        visibility = visibility or "partial"  # a v1 answer has no visibility field
        size = _enum_or_none(obj.get("animal_size"), SIZE_VALUES) or "none"
        location = _enum_or_none(obj.get("animal_location"), LOCATION_VALUES) or "none"
    confidence: Optional[float]
    if prompt_version == "v1":
        confidence = None
        if "confidence" in obj:
            try:
                confidence = min(1.0, max(0.0, float(obj.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                confidence = 0.0
    else:
        confidence = derived_confidence(visibility, evidence)
    return PresenceVerdict(
        has_animal=has_animal,
        has_person=has_person,
        confidence=confidence,
        description=str(obj.get("description") or ""),
        bbox=bbox_mapper(obj.get("bbox")),
        animal_visibility=visibility,
        animal_size=size,
        animal_location=location,
        visual_conditions=conditions,
        evidence=evidence,
        prompt_version=prompt_version,
    )


def parse_single_response(text: str, prompt_version: str = PROMPT_VERSION) -> PresenceVerdict:
    """One frame's JSON -> verdict; raises PresenceParseError on anything else."""
    obj = _load_json(text)
    if isinstance(obj, list) and len(obj) == 1:  # a model wrapping the object in a list
        obj = obj[0]
    return _verdict_from_obj(obj, bbox_1000_to_xywh, prompt_version)


def parse_sheet_response(text: str, sheet: PreparedImage, prompt_version: str = PROMPT_VERSION) -> list[Optional[PresenceVerdict]]:
    """Contact-sheet JSON -> one verdict per cell (None for a cell the model did not answer)."""
    obj = _load_json(text)
    cells = obj.get("cells") if isinstance(obj, dict) else obj
    if not isinstance(cells, list):
        raise PresenceParseError("contact sheet response has no cells list")
    n = len(sheet.cell_boxes)
    out: list[Optional[PresenceVerdict]] = [None] * n
    for pos, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        try:
            idx = int(cell.get("index", pos + 1)) - 1
        except (TypeError, ValueError):
            idx = pos
        if not 0 <= idx < n or out[idx] is not None:
            continue
        try:
            out[idx] = _verdict_from_obj(cell, lambda raw, i=idx: map_sheet_bbox_to_frame(raw, sheet, i), prompt_version)
        except PresenceParseError:
            continue
    return out


# ── Cost and estimates (pure) ────────────────────────────────────────


def estimate_call(
    prepared: PreparedImage,
    model: str,
    variant: str,
    media_resolution: str = DEFAULT_MEDIA_RESOLUTION,
    prompt_version: str = PROMPT_VERSION,
) -> tuple[int, int, float]:
    """Dry-run estimate for one call: (input_tokens, output_tokens, usd), no network.

    The v2 answer is longer (evidence list, conditions, description), so the
    assumed output tokens come from the per-version table in ``gemini_pricing``.
    """
    n = len(prepared.cell_boxes)
    inp = estimate_image_tokens(prepared.width, prepared.height, model, media_resolution) + estimate_text_tokens(
        build_prompt(variant, n, prompt_version)
    )
    per_frame, per_cell = ASSUMED_OUTPUT_TOKENS.get(prompt_version, ASSUMED_OUTPUT_TOKENS[PROMPT_VERSION])
    out = (per_cell if variant == "contact_sheet" else per_frame) * n
    return inp, out, cost_usd(model, inp, out, batch=(variant == "batch"))


# ── Network boundary (the only functions that touch the SDK) ─────────


def is_enabled() -> bool:
    from app.config import settings

    return bool(settings.GEMINI_API_KEY)


_client = None


def _get_client():
    """Process-wide ``genai.Client`` built from ``settings.GEMINI_API_KEY`` (never logged)."""
    global _client
    if _client is None:
        from google import genai

        from app.config import settings

        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set; the Gemini presence filter is disabled")
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _content_and_config(
    prepared: PreparedImage,
    variant: str,
    model: str,
    thinking_level: Optional[str],
    media_resolution: str,
    prompt_version: str = PROMPT_VERSION,
):
    """Build the SDK request pieces (parts + GenerateContentConfig) for one prepared image."""
    from google.genai import types

    res_enum = f"MEDIA_RESOLUTION_{media_resolution.upper()}"
    image_part = types.Part.from_bytes(data=prepared.data, mime_type=prepared.mime_type, media_resolution=res_enum)
    contents = [types.Part.from_text(text=build_prompt(variant, len(prepared.cell_boxes), prompt_version)), image_part]
    cfg: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_json_schema": response_json_schema(variant, prompt_version),
        "temperature": 0.0,
        # No tools are passed; disabling AFC silences the SDK's per-call warning.
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if thinking_level:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    return contents, types.GenerateContentConfig(**cfg)


def _generate_content(model: str, contents: list, config: Any) -> Any:
    """The synchronous generate call. Tests replace this."""
    return _get_client().models.generate_content(model=model, contents=contents, config=config)


def _is_quota_error(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code == 429 or "RESOURCE_EXHAUSTED" in str(exc)


def _is_transient_error(exc: BaseException) -> bool:
    """A dropped connection, DNS blip or timeout: worth waiting out, unlike a bad request."""
    if _is_quota_error(exc):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (500, 502, 503, 504):
        return True
    transport = {"ConnectError", "ReadError", "WriteError", "RemoteProtocolError", "ReadTimeout", "ConnectTimeout"}
    return type(exc).__name__ in transport or "getaddrinfo failed" in str(exc)


def _quota_wait_seconds(exc: BaseException) -> float:
    m = _RETRY_IN_RE.search(str(exc))
    return float(m.group(1)) + 1.0 if m else QUOTA_DEFAULT_WAIT_S


def _generate_with_backoff(model: str, contents: list, config: Any, sleep=time.sleep) -> Any:
    """``_generate_content`` with a bounded wait-and-retry on quota (429), 5xx and transport errors."""
    for attempt in range(1, QUOTA_RETRY_ATTEMPTS + 1):
        try:
            return _generate_content(model, contents, config)
        except Exception as exc:
            if not _is_transient_error(exc) or attempt == QUOTA_RETRY_ATTEMPTS:
                raise
            wait = _quota_wait_seconds(exc)
            logger.warning("gemini_presence_transient_wait", model=model, attempt=attempt, wait_s=wait, error=type(exc).__name__)
            sleep(wait)
    raise AssertionError("unreachable")


def _batch_create(model: str, src: list, display_name: str) -> Any:
    """Submit an inline Batch API job. Tests replace this."""
    return _get_client().batches.create(model=model, src=src, config={"display_name": display_name})


def _batch_get(name: str) -> Any:
    """Fetch a Batch API job by name. Tests replace this."""
    return _get_client().batches.get(name=name)


# ── Public API ───────────────────────────────────────────────────────


def _result_from_response(
    response: Any,
    prepared: PreparedImage,
    model: str,
    variant: str,
    latency_s: float,
    batch: bool,
    prompt_version: str = PROMPT_VERSION,
) -> PresenceResult:
    text = getattr(response, "text", None) or ""
    usage = TokenUsage.from_usage_metadata(getattr(response, "usage_metadata", None))
    n = len(prepared.cell_boxes)
    result = PresenceResult(
        model=model,
        variant=variant,
        verdicts=[None] * n,
        usage=usage,
        cost_usd=cost_usd(model, usage.input_tokens, usage.billed_output_tokens, batch=batch),
        latency_s=latency_s,
        raw_text=text,
        prompt_version=prompt_version,
    )
    try:
        if variant == "contact_sheet":
            result.verdicts = parse_sheet_response(text, prepared, prompt_version)
        else:
            result.verdicts = [parse_single_response(text, prompt_version)]
    except PresenceParseError as exc:
        result.error = str(exc)
        logger.warning("gemini_presence_parse_error", model=model, variant=variant, error=str(exc))
    return result


def prepare(images: Sequence[bytes], variant: str) -> PreparedImage:
    """Prepare ``images`` for ``variant`` (one frame for single/batch, a burst for contact_sheet)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown Gemini presence variant {variant!r}; expected one of {VARIANTS}")
    if variant == "contact_sheet":
        return prepare_contact_sheet(images)
    if len(images) != 1:
        raise ValueError(f"variant {variant!r} takes exactly one frame per call, got {len(images)}")
    return prepare_single(images[0])


def presence(
    images: Sequence[bytes],
    variant: str,
    model: str,
    *,
    thinking_level: Optional[str] = None,
    media_resolution: str = DEFAULT_MEDIA_RESOLUTION,
    prompt_version: str = PROMPT_VERSION,
    sleep=time.sleep,
) -> PresenceResult:
    """Ask ``model`` whether the frame(s) contain an animal, synchronously.

    ``variant`` ``single`` and ``batch`` take one frame; ``contact_sheet`` takes a
    burst of up to ``SHEET_MAX_CELLS`` frames. (``batch`` here is priced at the
    standard rate because it went through the online endpoint; use
    :func:`presence_batch` for the real Batch API.) Never raises on a bad answer:
    ``result.error`` is set and the verdicts stay None. A 429 quota answer is
    waited out and retried (``QUOTA_RETRY_ATTEMPTS``), then re-raised.
    """
    prepared = prepare(images, variant)
    contents, config = _content_and_config(prepared, variant, model, thinking_level, media_resolution, prompt_version)
    start = time.monotonic()
    response = _generate_with_backoff(model, contents, config, sleep=sleep)
    return _result_from_response(response, prepared, model, variant, time.monotonic() - start, batch=False, prompt_version=prompt_version)


def presence_batch(
    groups: Sequence[Sequence[bytes]],
    variant: str,
    model: str,
    *,
    thinking_level: Optional[str] = None,
    media_resolution: str = DEFAULT_MEDIA_RESOLUTION,
    poll_seconds: float = BATCH_POLL_SECONDS,
    max_wait_seconds: float = BATCH_MAX_WAIT_SECONDS,
    display_name: str = "ww-presence",
    prompt_version: str = PROMPT_VERSION,
    sleep=time.sleep,
) -> list[PresenceResult]:
    """Variant (c): submit every group as one inline Batch API request and wait for the job.

    ``variant`` selects the image layout of each request (``single``/``batch`` = one
    frame per request, ``contact_sheet`` = one burst per request); the results are
    priced at the Batch tier. Returns one result per group, in order; a request the
    job could not answer yields a result with ``error`` set. Raises ``TimeoutError``
    when the job is still running after ``max_wait_seconds`` (the job keeps running
    server-side; its name is in the log).
    """
    from google.genai import types

    layout = "contact_sheet" if variant == "contact_sheet" else "single"
    prepared = [prepare(g, layout) for g in groups]
    requests = []
    for p in prepared:
        parts, config = _content_and_config(p, layout, model, thinking_level, media_resolution, prompt_version)
        requests.append(types.InlinedRequest(contents=[types.Content(role="user", parts=parts)], config=config))
    start = time.monotonic()
    job = _batch_create(model, requests, display_name)
    name = getattr(job, "name", None)
    logger.info("gemini_presence_batch_submitted", job=name, requests=len(requests), model=model)
    while _state_name(job) not in _BATCH_DONE_STATES:
        if time.monotonic() - start > max_wait_seconds:
            raise TimeoutError(f"Gemini batch job {name} still {_state_name(job)} after {max_wait_seconds:.0f}s")
        sleep(poll_seconds)
        job = _batch_get(name)
    latency = (time.monotonic() - start) / max(1, len(requests))
    state = _state_name(job)
    logger.info("gemini_presence_batch_done", job=name, state=state)
    responses = list(getattr(getattr(job, "dest", None), "inlined_responses", None) or [])
    results: list[PresenceResult] = []
    for i, p in enumerate(prepared):
        inline = responses[i] if i < len(responses) else None
        response = getattr(inline, "response", None) if inline is not None else None
        if response is None:
            err = getattr(inline, "error", None) if inline is not None else f"no response (job state {state})"
            results.append(
                PresenceResult(model=model, variant="batch", verdicts=[None] * len(p.cell_boxes), error=str(err), prompt_version=prompt_version)
            )
            continue
        results.append(_result_from_response(response, p, model, "batch", latency, batch=True, prompt_version=prompt_version))
    return results


def _state_name(job: Any) -> str:
    state = getattr(job, "state", None)
    return str(getattr(state, "name", state) or "JOB_STATE_UNSPECIFIED")
