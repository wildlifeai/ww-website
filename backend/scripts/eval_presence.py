#!/usr/bin/env python3
# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""
eval_presence.py
================
Measure the Gemini animal-presence filter against labelled frames, per variant
and per model, and put SpeciesNet on the same table.

Reads the CSV written by ``label_presence.py`` (only ``animal``/``empty`` rows
count; ``unsure`` rows are listed but excluded from the metrics), runs the
requested variants over the frames, and writes a markdown table with, per
(variant, model): recall of animal frames, false-negative rate, precision,
empty frames removed (specificity), tokens per frame, USD per frame, USD per
1,000 frames and median latency, plus whether the T3 target is met (at least
85% of empty frames removed at under 1.5% false negatives).

Usage (from ``backend/``, ``GEMINI_API_KEY`` in the root ``.env`` for live runs)::

    # cost only, no API call
    python scripts/eval_presence.py labels.csv --dry-run --limit 50

    # live: all three variants on the default model, results cached per call
    python scripts/eval_presence.py labels.csv --variants single,contact_sheet,batch \\
        --models gemini-3.1-flash-lite --limit 200 --cache eval_cache.jsonl --out results.md

    # SpeciesNet on the same frames: dump once inside the dev Docker image
    # (needs torch + speciesnet), then hand the file to the comparison.
    python scripts/eval_presence.py labels.csv --dump-speciesnet speciesnet.json
    python scripts/eval_presence.py labels.csv --speciesnet-results speciesnet.json --dry-run

    # the same dump with and without the whole-frame and vehicle box rules (#285)
    python scripts/eval_presence.py labels.csv --variants "" --speciesnet-results speciesnet.json --speciesnet-rules

    # a new prompt on a chosen subset only (one path per line, as in the CSV)
    python scripts/eval_presence.py labels.csv --variants single --prompt-version v3 --only subset.txt \\
        --cache eval_cache_v3.jsonl --min-interval 4.2

``--cache`` is a JSONL of every live call keyed by (model, variant, frames,
prompt version), so re-running after a crash or adding a model never pays twice
for the same frames. A v1 cache is never reused for a v2 run: the key carries
``--prompt-version`` (v1 keys keep the original shape so the 2026-09-28 cache
still resumes a ``--prompt-version v1`` run). ``--only`` restricts every run to
the frames a file lists, so a prompt can be tried on a subset without editing
the labels.

Besides the headline table the script reports recall per stratum that needs no
extra labels: night IR vs day (``light_of``: the flash, else the WW500 exposure,
else the greyscale ratio, with a line counting each source), burst length
(1 / 2 / 3+ from the CSV's burst ids) and the top-level folder of each frame.
``--dump-verdicts`` writes every frame's verdict with the v2 structured fields.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.burst_evidence import DEFAULT_GAP_SECONDS  # noqa: E402
from app.domain.burst_evidence import group_bursts as group_media_bursts  # noqa: E402
from app.domain.exif import parse_exif_from_bytes  # noqa: E402
from app.services import gemini_presence as gp  # noqa: E402
from app.services.gemini_pricing import DEFAULT_MODEL, PRICE_READ_ON, PRICE_SOURCE_URL  # noqa: E402
from scripts.label_presence import make_frame, media_row  # noqa: E402

T3_MIN_EMPTY_REMOVED = 0.85
T3_MAX_FALSE_NEGATIVE_RATE = 0.015

# A frame whose colour channels differ by less than this (mean absolute
# difference on 0-255, after downscaling) is treated as greyscale, i.e. night IR.
# Only for frames without flash or exposure metadata: the WW500's HM0360 is
# monochrome, so every frame it takes is greyscale (#302).
GREYSCALE_MAX_CHANNEL_DIFF = 3.0

# Exposure (``exposure_of``: integration lines x analog gain x digital gain) at or
# above which a WW500 frame without a fired flash counts as night_ir: full
# integration (376 lines), analog code 4 (16x) and digital gain 2x, so the AE has
# run past the analog gain and is spending digital gain. Chosen from the labelled
# set (2026-09-26, Tommy_WW_Tests left out): integration is 376 lines on all 658
# frames with a MakerNote, and on the Colorado deployment the analog code climbs
# from 1 at 17:30 to 4 at 20:15 local time, daylight to dusk. The highest
# ambient-lit exposure is 6,110 (376, code 4, digital 65); the one frame above the
# cut (376, code 4, digital 192, ae_mean 5, not converged) is black. The firmware's
# flash rule (lightSensor.c, analog code above 2) would put 94 of these ambient-lit
# frames in night_ir.
LOW_LIGHT_MIN_EXPOSURE = 376 * 16 * 2


# ── Data ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LabelledFrame:
    path: str
    burst_id: str
    has_animal: Optional[bool]  # None = unsure or person
    label: str = ""


@dataclass
class FrameOutcome:
    """One frame's prediction with its share of the call's cost and latency."""

    path: str
    truth: Optional[bool]
    predicted: Optional[bool]  # None = no answer (parse error, API error)
    confidence: Optional[float] = None
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    # v2 structured fields (None / empty on v1 answers and unanswered frames).
    visibility: Optional[str] = None
    size: Optional[str] = None
    location: Optional[str] = None
    conditions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    description: str = ""
    prompt_version: str = ""
    has_person: Optional[bool] = None  # v3 only


@dataclass
class Metrics:
    frames: int = 0
    animal_frames: int = 0
    empty_frames: int = 0
    unanswered: int = 0
    tp: int = 0
    fn: int = 0
    fp: int = 0
    tn: int = 0
    tokens_per_frame: float = 0.0
    usd_per_frame: float = 0.0
    median_latency_s: float = 0.0

    @property
    def recall(self) -> Optional[float]:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def false_negative_rate(self) -> Optional[float]:
        return self.fn / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def precision(self) -> Optional[float]:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def empty_removed(self) -> Optional[float]:
        """Share of empty frames the filter would drop (specificity)."""
        return self.tn / (self.tn + self.fp) if (self.tn + self.fp) else None

    @property
    def usd_per_1000(self) -> float:
        return self.usd_per_frame * 1000

    @property
    def meets_t3(self) -> Optional[bool]:
        if self.empty_removed is None or self.false_negative_rate is None:
            return None
        return self.empty_removed >= T3_MIN_EMPTY_REMOVED and self.false_negative_rate < T3_MAX_FALSE_NEGATIVE_RATE


def read_labels(csv_path: str, root: Optional[str] = None) -> list[LabelledFrame]:
    """Last row per path wins; frames come back in CSV order of their labeller burst id then path.

    A relative ``path`` (a committed copy of the CSV) is resolved against ``root``.
    """
    rows: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("path"):
                path = row["path"]
                if root and not os.path.isabs(path):
                    path = os.path.join(root, path)
                rows[os.path.normpath(path)] = row
    frames: list[LabelledFrame] = []
    for path, row in rows.items():
        raw = (row.get("has_animal") or "").strip()
        truth = None if raw == "" else raw in ("1", "true", "True")
        label = (row.get("label") or "").strip()
        frames.append(LabelledFrame(path=path, burst_id=row.get("burst_id") or path, has_animal=truth, label=label))
    frames.sort(key=lambda f: (f.burst_id, f.path))
    return frames


def read_only_list(path: str, root: Optional[str] = None) -> set[str]:
    """Normalised paths from an ``--only`` file: one per line, ``#`` comments and blanks skipped, relative ones resolved against ``root``."""
    out: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if root and not os.path.isabs(entry):
                entry = os.path.join(root, entry)
            out.add(os.path.normpath(entry))
    return out


def filter_only(bursts: list[list[LabelledFrame]], only: Optional[set[str]]) -> list[list[LabelledFrame]]:
    """``bursts`` keeping only the frames in ``only`` (None keeps everything); bursts left empty are dropped."""
    if only is None:
        return bursts
    return [b for b in ([f for f in b if f.path in only] for b in bursts) if b]


def compute_bursts(frames: list[LabelledFrame], gap_seconds: float = DEFAULT_GAP_SECONDS) -> list[list[LabelledFrame]]:
    """Trigger bursts through the one grouper (``app.domain.burst_evidence.group_bursts``).

    Each frame's EXIF (or hex-name clock) is read from disk through
    ``label_presence.make_frame``; a frame that cannot be read is a singleton.
    Bursts come back in the frames' order of first appearance.
    """
    by_path = {f.path: f for f in frames}
    rows = []
    for f in frames:
        try:
            rows.append(media_row(make_frame(f.path)))
        except OSError:
            rows.append({"id": f.path, "deployment_id": os.path.dirname(f.path), "file_path": f.path, "timestamp": None})
    order = {f.path: i for i, f in enumerate(frames)}
    bursts = [[by_path[r["id"]] for r in b] for b in group_media_bursts(rows, gap_seconds)]
    return sorted(bursts, key=lambda b: min(order[f.path] for f in b))


def apply_limit(bursts: list[list[LabelledFrame]], limit: Optional[int]) -> list[list[LabelledFrame]]:
    """The first bursts holding at least ``limit`` frames (whole bursts, so contact sheets stay intact)."""
    if limit is None:
        return bursts
    out: list[list[LabelledFrame]] = []
    n = 0
    for b in bursts:
        if n >= limit:
            break
        out.append(b)
        n += len(b)
    return out


def work_units(frames: list[LabelledFrame], variant: str, bursts: Optional[list[list[LabelledFrame]]] = None) -> list[list[LabelledFrame]]:
    """Frames per API call: one each, or one burst chunk (<= SHEET_MAX_CELLS) for the contact sheet."""
    if variant == "contact_sheet":
        bursts = bursts if bursts is not None else compute_bursts(frames)
        return [b[i : i + gp.SHEET_MAX_CELLS] for b in bursts for i in range(0, len(b), gp.SHEET_MAX_CELLS)]
    return [[f] for f in frames]


# ── Metrics (pure, unit-tested) ──────────────────────────────────────


def compute_metrics(outcomes: Iterable[FrameOutcome]) -> Metrics:
    """Confusion counts over labelled frames; unanswered frames count as misses of their class."""
    m = Metrics()
    tokens: list[float] = []
    costs: list[float] = []
    latencies: list[float] = []
    for o in outcomes:
        if o.truth is None:
            continue
        m.frames += 1
        tokens.append(o.input_tokens + o.output_tokens)
        costs.append(o.cost_usd)
        if o.latency_s:
            latencies.append(o.latency_s)
        if o.truth:
            m.animal_frames += 1
        else:
            m.empty_frames += 1
        if o.predicted is None:
            m.unanswered += 1
            # A frame with no verdict cannot be filtered out, so it is kept: an
            # animal is not lost (counts as TP for recall) but an empty is not removed (FP).
            if o.truth:
                m.tp += 1
            else:
                m.fp += 1
            continue
        if o.truth and o.predicted:
            m.tp += 1
        elif o.truth and not o.predicted:
            m.fn += 1
        elif not o.truth and o.predicted:
            m.fp += 1
        else:
            m.tn += 1
    if m.frames:
        m.tokens_per_frame = sum(tokens) / m.frames
        m.usd_per_frame = sum(costs) / m.frames
    if latencies:
        m.median_latency_s = statistics.median(latencies)
    return m


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _key3(key: tuple) -> tuple[str, str, str]:
    """(variant, model, prompt) from a 2- or 3-tuple result key (SpeciesNet rows have no prompt)."""
    return (key[0], key[1], key[2] if len(key) > 2 else "")


def person_line(outcomes_by_run: dict[tuple, list[FrameOutcome]], person_paths: set[str]) -> str:
    """How often each run called a person frame an animal (a wildlife filter should not)."""
    if not person_paths:
        return ""
    parts = []
    for key, outs in outcomes_by_run.items():
        answered = [o for o in outs if o.path in person_paths and o.predicted is not None]
        called = sum(1 for o in answered if o.predicted)
        part = f"{' / '.join(str(k) for k in key)}: {called} of {len(answered)} answered"
        if any(o.has_person is not None for o in answered):  # v3 reports people on their own
            part += f", has_person on {sum(1 for o in answered if o.has_person)}"
        parts.append(part)
    return f"\nPerson frames ({len(person_paths)}, not counted above), called animal by " + "; ".join(parts) + ".\n"


def render_markdown(results: dict[tuple, Metrics], dry_run: bool, n_unsure: int = 0) -> str:
    """The results table plus the T3 verdict column."""
    header = (
        "| Variant | Model | Prompt | Frames | Recall (animal) | FN rate | Precision | Empty removed | Tokens/frame "
        "| USD/frame | USD/1,000 | Median latency | T3 (>=85% removed, <1.5% FN) |"
    )
    lines = [header, "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for key, m in results.items():
        variant, model, prompt = _key3(key)
        t3 = "n/a" if m.meets_t3 is None else ("yes" if m.meets_t3 else "no")
        lat = "est." if dry_run else f"{m.median_latency_s:.2f} s"
        rec, fnr, prec, rem = ("est.",) * 4 if dry_run else (_pct(m.recall), _pct(m.false_negative_rate), _pct(m.precision), _pct(m.empty_removed))
        if dry_run:
            t3 = "n/a"
        lines.append(
            f"| {variant} | {model} | {prompt or 'n/a'} | {m.frames} | {rec} | {fnr} | {prec} | {rem} | {m.tokens_per_frame:.0f} | "
            f"${m.usd_per_frame:.5f} | ${m.usd_per_1000:.2f} | {lat} | {t3} |"
        )
    note = f"\nPrices from {PRICE_SOURCE_URL} read on {PRICE_READ_ON}."
    if dry_run:
        note += " Dry run: tokens and USD are estimates from the documented image-token rules, no API call was made."
    if n_unsure:
        note += f" {n_unsure} frame(s) labelled unsure were excluded from the metrics."
    return "\n".join(lines) + "\n" + note + "\n"


# ── Strata (derived, no extra labels) ────────────────────────────────


def is_greyscale(data: bytes, max_channel_diff: float = GREYSCALE_MAX_CHANNEL_DIFF) -> bool:
    """True when the frame's colour channels agree within ``max_channel_diff``: a night IR capture."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((64, 64))
    px = list(img.getdata())
    if not px:
        return True
    diff = sum(max(r, g, b) - min(r, g, b) for r, g, b in px) / len(px)
    return diff <= max_channel_diff


def exposure_of(exif: dict) -> Optional[float]:
    """Integration lines x analog x digital gain from the WW500 MakerNote fields, or None without them.

    The analog code (ANALOG_GAIN bits 4-6, ``hm0360_md.c``) is read as a power of
    two and the digital gain as 64 = 1x. An all-zero read (no sensor) is None.
    """
    lines, analog, digital = (exif.get(k) for k in ("integration_lines", "analog_gain", "digital_gain"))
    if not lines or analog is None or not digital:
        return None
    return lines * 2**analog * digital / 64


def light_of(data: bytes) -> tuple[str, str]:
    """``(night_ir | day, source)`` for one frame, from the first source it carries.

    ``flash``: EXIF Flash (or the MakerNote copy) says the flash fired, night_ir; a
    flash that did not fire decides nothing, since the flash can be switched off.
    ``exposure``: the WW500 MakerNote AE fields against ``LOW_LIGHT_MIN_EXPOSURE``.
    ``greyscale``: the channel ratio, for frames with neither (other cameras).
    """
    exif = parse_exif_from_bytes(data)
    if exif.get("flash_fired"):
        return "night_ir", "flash"
    exposure = exposure_of(exif)
    if exposure is not None:
        return ("night_ir" if exposure >= LOW_LIGHT_MIN_EXPOSURE else "day"), "exposure"
    return ("night_ir" if is_greyscale(data) else "day"), "greyscale"


def light_line(strata: dict[str, dict[str, str]]) -> str:
    """How many frames got each light value and how many each source decided."""

    def _counts(by_path: dict[str, str]) -> str:
        return ", ".join(f"{k} {n}" for k, n in sorted(Counter(by_path.values()).items()))

    return f"Light ({len(strata['light'])} frames): {_counts(strata['light'])}; decided by {_counts(strata['light_source'])}.\n"


def top_folder_of(paths: Iterable[str]) -> Callable[[str], str]:
    """A function giving each path's first directory below the common root of all ``paths``."""
    normed = [os.path.normpath(p) for p in paths]
    try:
        root = os.path.commonpath(normed) if len(normed) > 1 else os.path.dirname(normed[0])
    except ValueError:  # different drives
        root = ""

    def _top(path: str) -> str:
        rel = os.path.relpath(os.path.normpath(path), root) if root else os.path.normpath(path)
        parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
        return parts[0] if len(parts) > 1 else "(root)"

    return _top


def burst_len_bucket(n: int) -> str:
    return "1" if n <= 1 else ("2" if n == 2 else "3+")


def strata_of(frames: list[LabelledFrame], bursts: list[list[LabelledFrame]], read: Callable[[str], bytes] = None) -> dict[str, dict[str, str]]:
    """``{stratum family: {path: stratum value}}`` for every frame: ``light``, ``light_source``, ``burst_len``, ``folder``."""
    read = read or (lambda p: open(p, "rb").read())
    sizes: dict[str, int] = {f.path: len(b) for b in bursts for f in b}
    top = top_folder_of([f.path for f in frames]) if frames else (lambda p: "(root)")
    light: dict[str, str] = {}
    source: dict[str, str] = {}
    for f in frames:
        try:
            light[f.path], source[f.path] = light_of(read(f.path))
        except Exception:
            light[f.path], source[f.path] = "unknown", "unreadable"
    return {
        "light": light,
        "light_source": source,
        "burst_len": {f.path: burst_len_bucket(sizes.get(f.path, 1)) for f in frames},
        "folder": {f.path: top(f.path) for f in frames},
    }


def metrics_by_stratum(outcomes: list[FrameOutcome], strata: dict[str, dict[str, str]]) -> dict[str, dict[str, Metrics]]:
    """``{family: {value: Metrics}}`` over the outcomes, sorted by value.

    Besides the frame-derived families in ``strata`` the model's own v2 labels
    give two more, ``gemini_size`` and ``gemini_location`` (animal frames only,
    the label the model returned, so a miss lands in ``none``).
    """
    out: dict[str, dict[str, Metrics]] = {}
    for family, by_path in strata.items():
        groups: dict[str, list[FrameOutcome]] = {}
        for o in outcomes:
            groups.setdefault(by_path.get(o.path, "unknown"), []).append(o)
        out[family] = {value: compute_metrics(g) for value, g in sorted(groups.items())}
    for family, attr in (("gemini_size", "size"), ("gemini_location", "location")):
        groups = {}
        for o in outcomes:
            if o.truth and getattr(o, attr):
                groups.setdefault(getattr(o, attr), []).append(o)
        if groups:
            out[family] = {value: compute_metrics(g) for value, g in sorted(groups.items())}
    return out


def render_strata_markdown(per_run: dict[tuple, dict[str, dict[str, Metrics]]]) -> str:
    """One table per (variant, model, prompt): recall and empty-removal per stratum."""
    blocks = []
    for key, families in per_run.items():
        variant, model, prompt = _key3(key)
        lines = [
            f"**{variant} / {model}" + (f" / prompt {prompt}**" if prompt else "**"),
            "",
            "| Stratum | Value | Frames | Animal | Recall (animal) | FN | Empty removed |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for family, values in families.items():
            for value, m in values.items():
                lines.append(f"| {family} | {value} | {m.frames} | {m.animal_frames} | {_pct(m.recall)} | {m.fn} | {_pct(m.empty_removed)} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# ── Runners ──────────────────────────────────────────────────────────


def _read_frames(unit: list[LabelledFrame]) -> list[bytes]:
    out = []
    for f in unit:
        with open(f.path, "rb") as fh:
            out.append(fh.read())
    return out


def _cache_key(model: str, variant: str, unit: list[LabelledFrame], prompt_version: str = gp.PROMPT_VERSION) -> str:
    """Cache key per call. v1 keeps the pre-versioning shape so the 2026-09-28 cache still resumes; any other version is keyed by it."""
    key = {"model": model, "variant": variant, "paths": [f.path for f in unit]}
    if prompt_version != "v1":
        key["prompt"] = prompt_version
    return json.dumps(key, sort_keys=True)


def _load_cache(path: Optional[str]) -> dict[str, dict]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {rec["key"]: rec for rec in (json.loads(line) for line in fh if line.strip())}


def _append_cache(path: Optional[str], record: dict) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _outcomes_from_result(unit: list[LabelledFrame], result_rec: dict) -> list[FrameOutcome]:
    n = len(unit)
    verdicts = result_rec.get("verdicts") or [None] * n
    return [
        FrameOutcome(
            path=f.path,
            truth=f.has_animal,
            predicted=None if v is None else bool(v["has_animal"]),
            confidence=None if v is None else v.get("confidence"),
            input_tokens=result_rec.get("input_tokens", 0) / n,
            output_tokens=result_rec.get("output_tokens", 0) / n,
            cost_usd=result_rec.get("cost_usd", 0.0) / n,
            latency_s=result_rec.get("latency_s", 0.0),
            visibility=None if v is None else v.get("animal_visibility"),
            size=None if v is None else v.get("animal_size"),
            location=None if v is None else v.get("animal_location"),
            conditions=list((v or {}).get("visual_conditions") or []),
            evidence=list((v or {}).get("evidence") or []),
            description=(v or {}).get("description") or "",
            prompt_version=result_rec.get("prompt_version", "v1"),
            has_person=None if v is None else v.get("has_person"),
        )
        for f, v in zip(unit, verdicts)
    ]


def _verdict_record(v: gp.PresenceVerdict) -> dict:
    return {
        "has_animal": v.has_animal,
        "has_person": v.has_person,
        "confidence": v.confidence,
        "description": v.description,
        "bbox": v.bbox,
        "animal_visibility": v.animal_visibility,
        "animal_size": v.animal_size,
        "animal_location": v.animal_location,
        "visual_conditions": list(v.visual_conditions),
        "evidence": list(v.evidence),
    }


def _record(result: gp.PresenceResult, key: str) -> dict:
    return {
        "key": key,
        "prompt_version": result.prompt_version,
        "verdicts": [None if v is None else _verdict_record(v) for v in result.verdicts],
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.billed_output_tokens,
        "cost_usd": result.cost_usd,
        "latency_s": result.latency_s,
        "error": result.error,
    }


def run_variant(
    frames: list[LabelledFrame],
    variant: str,
    model: str,
    *,
    dry_run: bool,
    cache_path: Optional[str],
    thinking_level: Optional[str] = None,
    min_interval: float = 0.0,
    prompt_version: str = gp.PROMPT_VERSION,
    max_calls: Optional[int] = None,
    bursts: Optional[list[list[LabelledFrame]]] = None,
) -> list[FrameOutcome]:
    """Outcomes for every frame; live calls are cached per (model, variant, frames, prompt version).

    ``max_calls`` caps the number of NEW live calls this run makes (a budget on a
    metered key); frames left uncalled come back unanswered. ``bursts`` (from
    :func:`compute_bursts`) shape the contact-sheet calls.
    """
    units = work_units(frames, variant, bursts)
    cache = _load_cache(cache_path)
    outcomes: list[FrameOutcome] = []
    if dry_run:
        for unit in units:
            layout = "contact_sheet" if variant == "contact_sheet" else "single"
            prepared = gp.prepare(_read_frames(unit), layout)
            inp, out, usd = gp.estimate_call(prepared, model, variant, prompt_version=prompt_version)
            outcomes.extend(
                _outcomes_from_result(
                    unit,
                    {"verdicts": [None] * len(unit), "input_tokens": inp, "output_tokens": out, "cost_usd": usd, "prompt_version": prompt_version},
                )
            )
        return outcomes

    if variant == "batch":
        todo = [u for u in units if _cache_key(model, variant, u, prompt_version) not in cache]
        if max_calls is not None:
            todo = todo[:max_calls]
        if todo:
            results = gp.presence_batch(
                [_read_frames(u) for u in todo],
                variant,
                model,
                thinking_level=thinking_level,
                display_name="ww-presence-eval",
                prompt_version=prompt_version,
            )
            for unit, result in zip(todo, results):
                rec = _record(result, _cache_key(model, variant, unit, prompt_version))
                cache[rec["key"]] = rec
                _append_cache(cache_path, rec)
    else:
        made = 0
        for i, unit in enumerate(units, 1):
            key = _cache_key(model, variant, unit, prompt_version)
            if key in cache:
                continue
            if max_calls is not None and made >= max_calls:
                break
            started = time.monotonic()
            result = gp.presence(_read_frames(unit), variant, model, thinking_level=thinking_level, prompt_version=prompt_version)
            made += 1
            rec = _record(result, key)
            cache[key] = rec
            _append_cache(cache_path, rec)
            print(
                f"  {variant}/{model}/{prompt_version} {i}/{len(units)} tokens={result.usage.total_tokens} "
                f"(in {result.usage.input_tokens}, out {result.usage.billed_output_tokens}) usd={result.cost_usd:.5f} {result.error or ''}",
                file=sys.stderr,
            )
            # Free-tier pacing: keep at most 60 / min_interval calls per minute.
            remaining = min_interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    for unit in units:
        rec = cache.get(_cache_key(model, variant, unit, prompt_version)) or {"verdicts": [None] * len(unit), "prompt_version": prompt_version}
        outcomes.extend(_outcomes_from_result(unit, rec))
    return outcomes


def dump_verdicts(outcomes_by_run: dict[tuple, list[FrameOutcome]], out_path: str) -> None:
    """Per-frame verdicts with the structured fields, one block per (variant, model, prompt)."""
    payload = {}
    for key, outcomes in outcomes_by_run.items():
        variant, model, prompt = _key3(key)
        payload[f"{variant}/{model}/{prompt or 'n/a'}"] = {
            o.path: {
                "truth": o.truth,
                "predicted": o.predicted,
                "has_person": o.has_person,
                "confidence": o.confidence,
                "animal_visibility": o.visibility,
                "animal_size": o.size,
                "animal_location": o.location,
                "visual_conditions": o.conditions,
                "evidence": o.evidence,
                "description": o.description,
                "prompt_version": o.prompt_version,
                "tokens": round(o.input_tokens + o.output_tokens, 1),
            }
            for o in outcomes
        }
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=1)


def apply_box_rules(verdict: dict, threshold: float, cutoffs) -> dict:
    """``verdict`` with ``has_animal``/``confidence`` recomputed from its raw detections after the box rules (#285).

    ``cutoffs`` is a ``domain.pipeline.DetectionCutoffs``; a verdict without
    ``detections`` (an older dump) is returned unchanged.
    """
    from types import SimpleNamespace

    detections = verdict.get("detections")
    if detections is None:
        return verdict
    animal = []
    for d in detections:
        det = SimpleNamespace(observation_type=d.get("type"), confidence=float(d.get("confidence") or 0.0), bbox=d.get("bbox"))
        if det.observation_type == "animal" and det.confidence >= threshold and cutoffs.drop_reason(det) is None:
            animal.append(det.confidence)
    return {**verdict, "has_animal": bool(animal), "confidence": max(animal, default=None)}


def speciesnet_outcomes(frames: list[LabelledFrame], results_path: str, cutoffs=None) -> list[FrameOutcome]:
    """Per-frame SpeciesNet verdicts from a ``--dump-speciesnet`` file: ``{path: {has_animal, ...}}``.

    With ``cutoffs`` the verdict is recomputed from the dumped detections at the
    dump's threshold with the box rules applied (``apply_box_rules``).
    """
    with open(results_path, encoding="utf-8") as fh:
        data = json.load(fh)
    verdicts = {os.path.normpath(k): v for k, v in data.get("frames", data).items()}
    threshold = float(data.get("threshold", 0.2)) if "frames" in data else 0.2
    out = []
    for f in frames:
        v = verdicts.get(f.path)
        if v is not None and cutoffs is not None:
            v = apply_box_rules(v, threshold, cutoffs)
        out.append(
            FrameOutcome(
                path=f.path, truth=f.has_animal, predicted=None if v is None else bool(v.get("has_animal")), confidence=(v or {}).get("confidence")
            )
        )
    return out


def dump_speciesnet(frames: list[LabelledFrame], out_path: str, threshold: float) -> None:
    """Run SpeciesNet over the frames (needs the ML deps: run inside the dev Docker image)."""
    import asyncio

    from app.services.speciesnet_service import get_speciesnet_service

    paths = [f.path for f in frames]
    start = time.monotonic()
    preds = asyncio.run(get_speciesnet_service().predict(paths))
    per_frame = {}
    for p in preds:
        animal = [d for d in p.detections if d.observation_type == "animal" and d.confidence >= threshold]
        per_frame[os.path.normpath(p.filepath)] = {
            "has_animal": bool(animal),
            "confidence": max((d.confidence for d in animal), default=None),
            "detections": [{"type": d.observation_type, "confidence": d.confidence, "bbox": d.bbox} for d in p.detections],
        }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"model": get_speciesnet_service().version, "threshold": threshold, "seconds": round(time.monotonic() - start, 1), "frames": per_frame},
            fh,
            indent=1,
        )
    print(f"SpeciesNet verdicts for {len(per_frame)} frames written to {out_path}")


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", help="CSV from label_presence.py")
    ap.add_argument("--root", default=None, help="folder that relative paths in the CSV are resolved against")
    ap.add_argument("--variants", default="single,contact_sheet,batch", help="comma-separated: single, contact_sheet, batch")
    ap.add_argument("--models", default=DEFAULT_MODEL, help="comma-separated Gemini model ids (each needs a price row)")
    ap.add_argument("--limit", type=int, default=None, help="use only the first N labelled frames (rounded up to whole bursts)")
    ap.add_argument(
        "--only",
        default=None,
        metavar="PATHS_TXT",
        help="use only the frames listed in this file, one path per line as in the CSV (relative ones resolved against --root)",
    )
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP_SECONDS, help="burst gap in seconds for the contact sheet and the burst_len stratum")
    ap.add_argument("--dry-run", action="store_true", help="estimate tokens and cost only; no API call")
    ap.add_argument("--cache", default=None, help="JSONL cache of live call results (resumable, never pays twice)")
    ap.add_argument("--out", default=None, help="write the markdown table here (default: stdout)")
    ap.add_argument("--thinking-level", default=None, help="Gemini 3 thinking_level to request (e.g. low); default leaves the model default")
    ap.add_argument("--min-interval", type=float, default=0.0, help="seconds between live calls (free tier: 15 per minute per model, so 4.2)")
    ap.add_argument(
        "--prompt-version",
        default=gp.PROMPT_VERSION,
        choices=gp.PROMPT_VERSIONS,
        help=f"prompt/schema version (default {gp.PROMPT_VERSION}); part of the cache key",
    )
    ap.add_argument("--max-calls", type=int, default=None, help="cap on NEW live calls per (variant, model) this run; the rest stay unanswered")
    ap.add_argument("--dump-verdicts", default=None, metavar="OUT_JSON", help="write every frame's verdict with the structured fields")
    ap.add_argument("--no-strata", action="store_true", help="skip the per-stratum recall tables (they open every frame once)")
    ap.add_argument("--speciesnet-results", default=None, help="JSON from --dump-speciesnet, adds a SpeciesNet row")
    ap.add_argument("--dump-speciesnet", default=None, metavar="OUT_JSON", help="run SpeciesNet over the frames and write verdicts (ML deps needed)")
    ap.add_argument("--speciesnet-threshold", type=float, default=0.2, help="detection confidence for --dump-speciesnet")
    ap.add_argument(
        "--speciesnet-rules",
        action="store_true",
        help="add a second SpeciesNet row with the whole-frame and vehicle box rules applied (cutoffs from the SPECIESNET_* settings)",
    )
    args = ap.parse_args(argv)

    bursts = all_bursts = compute_bursts(read_labels(args.labels, args.root), args.gap)
    if args.only:
        only = read_only_list(args.only, args.root)
        bursts = filter_only(bursts, only)
        found = sum(len(b) for b in bursts)
        if found < len(only):
            print(f"--only: {len(only) - found} listed path(s) are not in the labels", file=sys.stderr)
    bursts = apply_limit(bursts, args.limit)
    frames = [f for b in bursts for f in b]

    def _scored(f: LabelledFrame) -> bool:  # animal/empty for the metrics, person for its own line
        return f.has_animal is not None or f.label == "person"

    labelled = [f for f in frames if f.has_animal is not None]
    person_paths = {f.path for f in frames if f.label == "person"}
    scored = [f for f in frames if _scored(f)]
    scored_bursts = [b for b in ([f for f in b if _scored(f)] for b in bursts) if b]
    n_unsure = len(frames) - len(scored)
    print(
        f"{len(frames)} frames ({len(labelled)} labelled, {len(person_paths)} person, {n_unsure} unsure) in {len(bursts)} bursts",
        file=sys.stderr,
    )

    if args.dump_speciesnet:
        dump_speciesnet(frames, args.dump_speciesnet, args.speciesnet_threshold)
        return 0

    results: dict[tuple, Metrics] = {}
    outcomes_by_run: dict[tuple, list[FrameOutcome]] = {}
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for variant in variants:
        if variant not in gp.VARIANTS:
            ap.error(f"unknown variant {variant!r}; expected one of {gp.VARIANTS}")
        for model in models:
            outcomes = run_variant(
                scored,
                variant,
                model,
                dry_run=args.dry_run,
                cache_path=args.cache,
                thinking_level=args.thinking_level,
                min_interval=args.min_interval,
                prompt_version=args.prompt_version,
                max_calls=args.max_calls,
                bursts=scored_bursts,
            )
            outcomes_by_run[(variant, model, args.prompt_version)] = outcomes
            results[(variant, model, args.prompt_version)] = compute_metrics(outcomes)
    if args.speciesnet_results:
        sn = speciesnet_outcomes(labelled, args.speciesnet_results)
        outcomes_by_run[("speciesnet", "speciesnet (local)")] = sn
        results[("speciesnet", "speciesnet (local)")] = compute_metrics(sn)
        if args.speciesnet_rules:
            from app.domain.pipeline import DetectionCutoffs

            cutoffs = DetectionCutoffs.from_config({})
            sn_rules = speciesnet_outcomes(labelled, args.speciesnet_results, cutoffs)
            key = ("speciesnet", f"speciesnet + box rules ({cutoffs.audit()})")
            outcomes_by_run[key] = sn_rules
            results[key] = compute_metrics(sn_rules)

    table = render_markdown(results, args.dry_run, n_unsure)
    if not args.dry_run:
        table += person_line(outcomes_by_run, person_paths)
    if not args.dry_run and not args.no_strata and labelled:
        # Burst length comes from the whole labelled set, so --only does not shorten a burst.
        strata = strata_of(labelled, [b for b in ([f for f in b if f.has_animal is not None] for b in all_bursts) if b])
        # Only frames that were actually answered say anything about a stratum.
        per_run = {key: metrics_by_stratum([o for o in outs if o.predicted is not None], strata) for key, outs in outcomes_by_run.items()}
        table += "\n### Recall by stratum (answered frames only)\n\n" + light_line(strata) + "\n" + render_strata_markdown(per_run)
    if args.dump_verdicts:
        dump_verdicts(outcomes_by_run, args.dump_verdicts)
        print(f"verdicts written to {args.dump_verdicts}", file=sys.stderr)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(table)
        print(f"written {args.out}", file=sys.stderr)
    else:
        print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
