# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemini API price table and image-token rules, the ONE place they are kept.

Every USD figure the presence filter reports (per call, per frame, per 1,000
frames) is computed from this module, so a price change is a one-line edit
here and nowhere else. Update ``PRICE_READ_ON`` whenever the numbers are
re-checked against the source pages.

Sources (read on ``PRICE_READ_ON``):

- Prices:            https://ai.google.dev/gemini-api/docs/pricing
- Model ids:         https://ai.google.dev/gemini-api/docs/models
- Image tokens:      https://ai.google.dev/gemini-api/docs/image-understanding
                     and https://ai.google.dev/gemini-api/docs/tokens
- Gemini 3 images:   https://ai.google.dev/gemini-api/docs/media-resolution
- Batch discount:    https://ai.google.dev/gemini-api/docs/batch-mode

Image tokenisation, as documented on 2026-09-26:

- Tile rule (the tokens and image-understanding pages): "Images <=384 pixels
  in both dimensions count as 258 tokens. Larger images are tiled into 768x768
  pixel tiles, each counting as 258 tokens." A WW500 frame is 640x480, so it
  already bills as ONE tile.
- Gemini 3 models bill an image by ``media_resolution`` instead (the
  media-resolution page): low 280, medium 560, high 1120 (the default when
  unspecified), ultra_high 2240 tokens per image, whatever its pixel size. The
  page says the exact count "depends on both the media type and the model
  version", so treat the table as the estimate and the response's
  ``usage_metadata`` as the truth; ``scripts/eval_presence.py`` prints both.

Prices are USD per one million tokens, paid tier, text/image input. The Batch
API is documented at 50% of the standard price for every model listed.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICE_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
PRICE_READ_ON = "2026-09-26"


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for one model id (standard and Batch API tiers)."""

    input_usd_per_m: float
    output_usd_per_m: float
    batch_input_usd_per_m: float
    batch_output_usd_per_m: float
    note: str = ""


# Cheapest open-access tier first. The 2.5 family is kept for completeness but the
# models page (2026-09-26) says Google is "limiting access to the 2.5 models to users
# who have actively used them in the past", so a fresh AI Studio key may not see them.
MODEL_PRICES: dict[str, ModelPrice] = {
    "gemini-3.1-flash-lite": ModelPrice(0.25, 1.50, 0.125, 0.75, "cheapest open tier by price per token"),
    "gemini-3.5-flash-lite": ModelPrice(0.30, 2.50, 0.15, 1.25, "documented as the fastest, most cost-effective tier"),
    "gemini-3.8-flash": ModelPrice(0.75, 3.75, 0.375, 1.875, "promotional through 2026-12-31, then 1.50 / 7.50"),
    "gemini-3.5-flash": ModelPrice(1.50, 9.00, 0.75, 4.50),
    "gemini-2.5-flash-lite": ModelPrice(0.10, 0.40, 0.05, 0.20, "access limited to prior users"),
    "gemini-2.5-flash": ModelPrice(0.30, 2.50, 0.15, 1.25, "access limited to prior users"),
}

DEFAULT_MODEL = "gemini-3.1-flash-lite"

# Tile rule (pre-Gemini-3 models).
TILE_TOKENS = 258
TILE_PX = 768
SMALL_IMAGE_MAX_PX = 384

# Gemini 3 per-image token allocation by media_resolution (documented values).
# Observed on 2026-09-26 with gemini-3.1-flash-lite at MEDIA_RESOLUTION_LOW, from
# usage_metadata minus the count_tokens prompt cost: 266 tokens for a 640x480
# frame and 270 for a 764x285 contact sheet, so the image is billed as a fixed
# block near the documented 280 and NOT by 768 px tiles. The table keeps the
# documented figure, which is the conservative estimate.
MEDIA_RESOLUTION_TOKENS: dict[str, int] = {
    "low": 280,
    "medium": 560,
    "high": 1120,
    "ultra_high": 2240,
}
GEMINI3_DEFAULT_MEDIA_RESOLUTION = "high"

# What a strict-JSON presence verdict costs in output tokens, used only by the
# dry-run estimate (no API call), keyed by prompt version as
# (per single frame, per contact-sheet cell). Observed on gemini-3.1-flash-lite:
# v1 (2026-09-26): 46 to 79 per single frame, about 70 to 94 per cell (the index
# field and the longer descriptions). v2 (2026-09-29 smoke test, 20 single
# frames): 93 to 151 per frame, empty frames at the low end (report section
# 6.8.7); the sheet-cell figure is the frame figure plus the v1 cell overhead.
ASSUMED_OUTPUT_TOKENS: dict[str, tuple[int, int]] = {
    "v1": (60, 90),
    "v2": (120, 150),
}
ASSUMED_OUTPUT_TOKENS_PER_FRAME = ASSUMED_OUTPUT_TOKENS["v1"][0]
ASSUMED_OUTPUT_TOKENS_PER_SHEET_CELL = ASSUMED_OUTPUT_TOKENS["v1"][1]


def uses_media_resolution(model: str) -> bool:
    """True when ``model`` bills images by media_resolution (Gemini 3 family)."""
    return model.startswith("gemini-3")


def estimate_image_tokens(width: int, height: int, model: str, media_resolution: str = "low") -> int:
    """Estimated input tokens for one image of ``width`` x ``height`` pixels on ``model``.

    Gemini 3 models: the media_resolution table (``media_resolution`` must be a key
    of ``MEDIA_RESOLUTION_TOKENS``). Older models: the 384 px / 768 px tile rule.
    """
    if uses_media_resolution(model):
        return MEDIA_RESOLUTION_TOKENS[media_resolution]
    if width <= SMALL_IMAGE_MAX_PX and height <= SMALL_IMAGE_MAX_PX:
        return TILE_TOKENS
    tiles_w = -(-width // TILE_PX)  # ceil division
    tiles_h = -(-height // TILE_PX)
    return tiles_w * tiles_h * TILE_TOKENS


def estimate_text_tokens(text: str) -> int:
    """Rough prompt-token estimate (about four characters per token for English)."""
    return max(1, len(text) // 4)


def model_price(model: str) -> ModelPrice:
    """The price row for ``model``; raises so an unknown id is added here, not guessed."""
    try:
        return MODEL_PRICES[model]
    except KeyError as exc:
        raise ValueError(f"No price row for Gemini model {model!r}; add it to services/gemini_pricing.py from {PRICE_SOURCE_URL}") from exc


def cost_usd(model: str, input_tokens: int, output_tokens: int, batch: bool = False) -> float:
    """USD for one call given its token counts. ``output_tokens`` must include thinking tokens."""
    price = model_price(model)
    in_rate = price.batch_input_usd_per_m if batch else price.input_usd_per_m
    out_rate = price.batch_output_usd_per_m if batch else price.output_usd_per_m
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
