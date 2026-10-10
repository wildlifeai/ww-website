# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""Burst grouping and evidence fusion, pure domain logic (no I/O, no HTTP).

Implements sections 5 (burst grouper rules), 6 (evidence score v1) and the
row-derived parts of 7 (consensus type) of the evidence-pipeline architecture
report (``documentation/development reports/2026-09_evidence-pipeline-architecture``).
The fusion step (``pipeline.py``, ``EvidenceFusionStep``) does the I/O around
these functions and writes the consensus row.

Bursts (section 5): within one deployment and one folder (the directory part
of ``media.file_path``), the firmware capture-sequence tag (``trigger_id`` and
``frame_index`` in ``media.exif_metadata``; not emitted by the firmware yet, so
absence means "no tag") groups first; otherwise consecutive frames whose
``media.timestamp`` differ by at most ``gap_seconds`` (``BURST_GAP_SECONDS``,
10 s: today's firmware spaces the frames of one trigger 3 to 5 s apart). A
frame without a timestamp is a singleton. Tagged and untagged frames never
share a burst. ``burst_index`` is 0-based (``frame_index`` when tagged).
``burst_id`` is ``<deployment_id>:<trigger_id>`` or ``<deployment_id>:<first
media id>``. This is the ONE grouper: ``media_registry.group_bursts`` (motion
ROI), the Gemini contact sheet, the labeller and the eval script all call it.

Score (section 6): a clamped weighted sum over the signals that are PRESENT.
An absent signal is ``None`` and adds nothing; it is never stored as 0. The
weights are hand-set guesses (``WEIGHTS_VERSION = "v1"``) to be replaced by
the disagreement matrix once it has human-labelled cells. Two cut points:
``THRESHOLD_V1`` (consensus animal at or above) and ``SUSPICIOUS_V1`` (the
band below the threshold that goes to the top of the review queue).
"""

from __future__ import annotations

import posixpath
from datetime import datetime
from typing import Any, Iterable, Optional

WEIGHTS_VERSION = "v1"
WEIGHTS_V1: dict[str, float] = {
    "speciesnet_presence": 0.50,
    "gemini_presence": 0.50,
    "neighbour_animal": 0.25,
    "motion": 0.15,
    "edge_presence": 0.15,
    "near_threshold": 0.10,
}
THRESHOLD_V1 = 0.50  # score >= threshold -> consensus 'animal'
SUSPICIOUS_V1 = 0.25  # [SUSPICIOUS, THRESHOLD): blank, top of the review queue
DEFAULT_GAP_SECONDS = 10.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.2  # SpeciesNet's detection cutoff, the run's confidence_threshold

# motion = min(1, motion_frac / MOTION_FULL_FRAC); a guess (the ROI code's own floor is
# 0.001). Above MOTION_CAMERA_SHIFT_FRAC the whole frame changed (camera shift, light
# change): motion is 0 and the frame is flagged for the quality stage.
MOTION_FULL_FRAC = 0.02
MOTION_CAMERA_SHIFT_FRAC = 0.6

# Fusion input for gemini_presence by visibility label (section 6.1); a v1 verdict has no label.
GEMINI_PRESENCE_BY_VISIBILITY = {"clear": 1.0, "partial": 1.0, "obscured": 0.8, "none": 0.0}
GEMINI_PRESENCE_V1 = 1.0

# Row provenance prefixes used to tell the writers apart (source_model_version).
SPECIESNET_MODEL_PREFIX = "speciesnet"
GEMINI_MODEL_PREFIX = "gemini"
NON_BLANK_TYPES = ("animal", "human", "vehicle", "unknown")


# ── Burst grouping ───────────────────────────────────────────────────


def folder_of(file_path: Optional[str]) -> str:
    """Directory part of a media ``file_path`` (any provider prefix, either slash)."""
    return posixpath.dirname((file_path or "").replace("\\", "/"))


def timestamp_seconds(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def sequence_tag(row: dict) -> tuple[Optional[str], Optional[int]]:
    """``(trigger_id, frame_index)`` from ``exif_metadata``; ``(None, None)`` when untagged."""
    exif = row.get("exif_metadata") or {}
    if not isinstance(exif, dict):
        return None, None
    trigger = exif.get("trigger_id")
    if trigger is None or str(trigger).strip() == "":
        return None, None
    index = exif.get("frame_index")
    try:
        index = int(index) if index is not None else None
    except (TypeError, ValueError):
        index = None
    return str(trigger).strip(), index


def burst_id_of(burst: list[dict]) -> str:
    """``<deployment_id>:<trigger_id>`` for a tagged burst, else ``<deployment_id>:<first media id>``."""
    first = burst[0]
    trigger, _ = sequence_tag(first)
    return f"{first.get('deployment_id')}:{trigger if trigger else first.get('id')}"


def _file_name(row: dict) -> str:
    return str(row.get("file_name") or posixpath.basename(str(row.get("file_path") or "").replace("\\", "/")) or row.get("id") or "")


def group_bursts(media_rows: Iterable[dict], gap_seconds: float = DEFAULT_GAP_SECONDS) -> list[list[dict]]:
    """Split media rows into trigger bursts (tag first, then timestamp gap), never across deployment or folder.

    A tagged burst is ordered by ``frame_index`` (then timestamp, then file
    name); an untagged one by timestamp then file name. Bursts come back ordered
    by deployment, folder and first frame; singletons without a timestamp last
    within their scope.
    """
    tagged: dict[tuple[str, str, str], list[dict]] = {}
    untagged: dict[tuple[str, str], list[dict]] = {}
    for row in media_rows:
        scope = (str(row.get("deployment_id") or ""), folder_of(row.get("file_path")))
        trigger, _ = sequence_tag(row)
        if trigger is not None:
            tagged.setdefault((*scope, trigger), []).append(row)
        else:
            untagged.setdefault(scope, []).append(row)

    def _tag_order(r: dict):
        index = sequence_tag(r)[1]
        return (index if index is not None else 1 << 30, timestamp_seconds(r.get("timestamp")) or 0.0, _file_name(r))

    bursts: list[list[dict]] = []
    for key in sorted(tagged):
        bursts.append(sorted(tagged[key], key=_tag_order))
    for scope in sorted(untagged):
        rows = untagged[scope]
        timed = sorted(
            (r for r in rows if timestamp_seconds(r.get("timestamp")) is not None),
            key=lambda r: (timestamp_seconds(r.get("timestamp")), _file_name(r)),
        )
        cur: list[dict] = []
        prev: Optional[float] = None
        for r in timed:
            ts = timestamp_seconds(r.get("timestamp"))
            if cur and prev is not None and ts is not None and (ts - prev) <= gap_seconds:
                cur.append(r)
            else:
                if cur:
                    bursts.append(cur)
                cur = [r]
            prev = ts
        if cur:
            bursts.append(cur)
        bursts.extend([r] for r in sorted((r for r in rows if timestamp_seconds(r.get("timestamp")) is None), key=_file_name))
    return bursts


def burst_index_of(burst: list[dict], position: int) -> int:
    """0-based index in the burst: the tag's 1-based ``frame_index`` minus one, else the position."""
    _, index = sequence_tag(burst[position])
    return index - 1 if index is not None and index >= 1 else position


# ── Reading the rows ─────────────────────────────────────────────────


def is_speciesnet_row(obs: dict) -> bool:
    return obs.get("source_type") == "ai" and str(obs.get("source_model_version") or "").startswith(SPECIESNET_MODEL_PREFIX)


def is_gemini_row(obs: dict) -> bool:
    return obs.get("source_type") == "ai" and str(obs.get("source_model_version") or "").startswith(GEMINI_MODEL_PREFIX)


# Camera AI rows of these types are not animal evidence (#135).
NON_ANIMAL_EDGE_TYPES = frozenset({"human", "vehicle"})


def is_edge_row(obs: dict) -> bool:
    return obs.get("source_type") == "ai" and obs.get("ai_origin") == "edge"


def speciesnet_type(observations: Iterable[dict]) -> Optional[str]:
    """SpeciesNet's kept non-blank type for the media (highest confidence first), ``blank`` when only a blank row, None when no row."""
    rows = [o for o in observations if is_speciesnet_row(o)]
    if not rows:
        return None
    kept = [o for o in rows if o.get("observation_type") in NON_BLANK_TYPES]
    if not kept:
        return "blank"
    return max(kept, key=lambda o: float(o.get("confidence") or 0.0))["observation_type"]


def gemini_verdict(observations: Iterable[dict]) -> Optional[tuple[bool, Optional[str]]]:
    """``(has_animal, visibility label or None for a v1 answer)`` from the latest Gemini row, None when there is none."""
    from app.services.gemini_presence import parse_verdict_comment

    rows = [o for o in observations if is_gemini_row(o)]
    if not rows:
        return None
    latest = max(rows, key=lambda o: str(o.get("classification_timestamp") or ""))
    has_animal = latest.get("observation_type") == "animal"
    parsed = parse_verdict_comment(latest.get("observation_comments"))
    return has_animal, parsed.get("visibility")


def has_model_presence(observations: Iterable[dict]) -> bool:
    """SpeciesNet presence or Gemini has_animal for this media (the neighbour test)."""
    obs = list(observations)
    sn = speciesnet_type(obs)
    gem = gemini_verdict(obs)
    return (sn is not None and sn != "blank") or (gem is not None and gem[0])


def edge_signals(observations: Iterable[dict], exif_metadata: Any) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """``(edge_presence, edge_score, label)``: 1 with an edge animal row; 0 when the EXIF carried NN scores and none cleared; absent otherwise.

    Presence means animal presence, so an edge row typed ``human`` or ``vehicle`` (a class that
    predicts a type, #135) does not count; the frame is then judged from the EXIF scores as if
    the camera saw no animal. "NN scores" is approximated as any numeric percentage in
    ``user_comment_fields`` (the label map that tells targets from telemetry lives with the
    project model).
    """
    rows = [o for o in observations if is_edge_row(o) and o.get("observation_type") not in NON_ANIMAL_EDGE_TYPES]
    if rows:
        best = max(rows, key=lambda o: float(o.get("classification_probability") or o.get("confidence") or 0.0))
        score = best.get("classification_probability")
        if score is None:
            score = best.get("confidence")
        label = best.get("vernacular_name") or best.get("scientific_name")
        return 1.0, (None if score is None else float(score)), (str(label) if label else None)
    fields = (exif_metadata or {}).get("user_comment_fields") if isinstance(exif_metadata, dict) else None
    if isinstance(fields, dict):
        for value in fields.values():
            try:
                float(str(value).strip().rstrip("%"))
                return 0.0, None, None
            except (TypeError, ValueError):
                continue
    return None, None, None


# ── Derived fusion inputs ────────────────────────────────────────────


def motion_input(motion_frac: Optional[float]) -> tuple[Optional[float], bool]:
    """``(motion, camera_shift_flag)``: ``min(1, motion_frac / 0.02)``; above 0.6 the frame is flagged and motion is 0."""
    if motion_frac is None:
        return None, False
    if motion_frac > MOTION_CAMERA_SHIFT_FRAC:
        return 0.0, True
    return min(1.0, max(0.0, motion_frac) / MOTION_FULL_FRAC), False


def near_threshold_input(speciesnet_max_conf: Optional[float], confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> Optional[float]:
    """``min(1, max_conf / threshold)``; absent when the max confidence is unknown."""
    if speciesnet_max_conf is None or confidence_threshold <= 0:
        return None
    return min(1.0, max(0.0, float(speciesnet_max_conf)) / confidence_threshold)


def gemini_presence_input(verdict: Optional[tuple[bool, Optional[str]]]) -> Optional[float]:
    """1.0 for clear or partial, 0.8 for obscured, 1.0 for a v1 answer, 0 when has_animal is false, absent without a row."""
    if verdict is None:
        return None
    has_animal, visibility = verdict
    if not has_animal:
        return 0.0
    if visibility is None:
        return GEMINI_PRESENCE_V1
    return GEMINI_PRESENCE_BY_VISIBILITY.get(visibility, GEMINI_PRESENCE_V1)


def frame_signals(
    media: dict,
    observations: Iterable[dict],
    *,
    speciesnet_max_conf: Optional[float] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    motion_frac: Optional[float] = None,
    burst_id: str = "",
    burst_index: int = 0,
    burst_len: int = 1,
    burst_animal_count: int = 0,
) -> dict[str, Any]:
    """One frame's fusion inputs and bookkeeping (section 6.1); an absent signal is None.

    ``observations`` are this media's live rows. ``speciesnet_max_conf`` comes from
    ``media_evidence`` (the SpeciesNet step records it before the threshold
    filter); None when unknown. ``motion_frac`` is None for a singleton or when the
    burst's other frames could not be resolved. ``neighbour_animal`` is absent for
    a singleton, not 0.
    """
    obs = list(observations)
    sn_type = speciesnet_type(obs)
    gem = gemini_verdict(obs)
    motion, camera_shift = motion_input(motion_frac)
    edge_presence, edge_score, edge_label = edge_signals(obs, media.get("exif_metadata"))
    return {
        "speciesnet_presence": None if sn_type is None else (1.0 if sn_type != "blank" else 0.0),
        "speciesnet_type": sn_type,
        "speciesnet_max_conf": None if speciesnet_max_conf is None else float(speciesnet_max_conf),
        "near_threshold": near_threshold_input(speciesnet_max_conf, confidence_threshold),
        "gemini_presence": gemini_presence_input(gem),
        "gemini_visibility": None if gem is None else gem[1],
        "motion_frac": None if motion_frac is None else float(motion_frac),
        "motion": motion,
        "camera_shift": camera_shift,
        "edge_presence": edge_presence,
        "edge_score": edge_score,
        "edge_label": edge_label,
        "burst_id": burst_id or burst_id_of([media]),
        "burst_index": int(burst_index),
        "burst_len": int(burst_len),
        "burst_animal_count": int(burst_animal_count),
        "neighbour_animal": None if burst_len < 2 else (1.0 if burst_animal_count >= 1 else 0.0),
    }


def burst_signals(
    burst: list[dict],
    observations_by_media: dict[str, list[dict]],
    motion_fracs: Optional[list[Optional[float]]] = None,
    speciesnet_max_conf: Optional[dict[str, float]] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """``frame_signals`` for every frame of one burst with the burst-level fields filled in.

    ``burst_animal_count`` is the number of OTHER frames in the burst with
    SpeciesNet or Gemini presence (section 5).
    """
    n = len(burst)
    fracs: list[Optional[float]] = list(motion_fracs) if motion_fracs is not None else [None] * n
    if len(fracs) != n:
        raise ValueError(f"motion_fracs has {len(fracs)} entries for a burst of {n} frames")
    if n < 2:
        fracs = [None] * n
    flags = [has_model_presence(observations_by_media.get(m.get("id"), [])) for m in burst]
    total = sum(flags)
    bid = burst_id_of(burst)
    conf = speciesnet_max_conf or {}
    return [
        frame_signals(
            m,
            observations_by_media.get(m.get("id"), []),
            speciesnet_max_conf=conf.get(m.get("id")),
            confidence_threshold=confidence_threshold,
            motion_frac=fracs[i],
            burst_id=bid,
            burst_index=burst_index_of(burst, i),
            burst_len=n,
            burst_animal_count=total - (1 if flags[i] else 0),
        )
        for i, m in enumerate(burst)
    ]


# ── Scoring ──────────────────────────────────────────────────────────


def evidence_score(signals: dict[str, Any], weights: dict[str, float] = WEIGHTS_V1) -> tuple[float, dict[str, Optional[float]]]:
    """Clamped weighted sum over the weighted signals that are present, and each one's contribution (None when absent)."""
    contributions: dict[str, Optional[float]] = {}
    total = 0.0
    for name, weight in weights.items():
        value = signals.get(name)
        if value is None:
            contributions[name] = None
            continue
        v = min(1.0, max(0.0, float(value)))
        contributions[name] = round(weight * v, 4)
        total += weight * v
    return round(max(0.0, min(1.0, total)), 4), contributions


def consensus_type(score: float, speciesnet_kept_type: Optional[str] = None, threshold: float = THRESHOLD_V1) -> str:
    """SpeciesNet's kept non-blank type when it has one; else ``animal`` at or above the threshold; else ``blank``."""
    if speciesnet_kept_type in NON_BLANK_TYPES:
        return speciesnet_kept_type
    return "animal" if score >= threshold else "blank"


def band_of(score: float, threshold: float = THRESHOLD_V1, suspicious: float = SUSPICIOUS_V1) -> str:
    """``animal`` / ``suspicious`` / ``confirmed_blank`` (section 6.2)."""
    if score >= threshold:
        return "animal"
    return "suspicious" if score >= suspicious else "confirmed_blank"


def _fmt(value: Optional[float]) -> str:
    return "absent" if value is None else f"{value:g}" if float(value).is_integer() else f"{value:.2f}"


def audit_line(score: float, threshold: float, signals: dict[str, Any], version: str = "evidence_fusion_v1", cutoffs: str = "") -> str:
    """The consensus row's comment (section 7): ``evidence_fusion_v1 score=0.91 threshold=0.50 speciesnet=0 gemini=1.0 ...``.

    ``cutoffs``, when given, is appended as is: the SpeciesNet box cutoffs of the run
    (``det=0.20 frame_area=0.90 frame_conf=0.50 vehicle=dropped``, #285).
    """
    line = (
        f"{version} score={score:.2f} threshold={threshold:.2f} "
        f"speciesnet={_fmt(signals.get('speciesnet_presence'))} gemini={_fmt(signals.get('gemini_presence'))} "
        f"neighbour={_fmt(signals.get('neighbour_animal'))} motion={_fmt(signals.get('motion'))} "
        f"edge={_fmt(signals.get('edge_presence'))} near={_fmt(signals.get('near_threshold'))}"
    )
    return f"{line} {cutoffs}" if cutoffs else line
