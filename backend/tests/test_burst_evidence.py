# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""domain/burst_evidence.py: burst grouping (tag, gap, folder, deployment), signals, the v1 score and motion fractions. Pure.

The numbers follow sections 5 and 6 of the evidence-pipeline architecture report.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from app.domain import burst_evidence as be
from app.domain.motion_roi import compute_motion_fractions

DEP = "d0000000-0000-4000-8000-000000000001"
DEP2 = "d0000000-0000-4000-8000-000000000002"
SN = "speciesnet-v4.0.1a"
GEM = "gemini-3.1-flash-lite"


def _media(mid, ts=None, folder="MEDIA/A", name=None, dep=DEP, exif=None):
    return {
        "id": mid,
        "deployment_id": dep,
        "file_path": f"gdrive://{folder}/{name or mid}.jpg",
        "file_name": f"{name or mid}.jpg",
        "timestamp": ts,
        "exif_metadata": exif,
    }


def _ts(sec: float) -> str:
    whole = int(sec)
    frac = sec - whole
    return f"2026-06-16T17:42:{whole:02d}{('.' + str(int(frac * 10))) if frac else ''}+00:00"


# ── Grouping ─────────────────────────────────────────────────────────


def test_group_bursts_by_timestamp_gap_default_ten_seconds():
    # Colorado frames 194749, 194752, 194756: today's firmware spaces one trigger 3 to 5 s apart.
    rows = [_media("a", _ts(0)), _media("b", _ts(3)), _media("c", _ts(7)), _media("d", _ts(18)), _media("e", _ts(19.5))]
    assert be.DEFAULT_GAP_SECONDS == 10.0
    bursts = be.group_bursts(rows)
    assert [[m["id"] for m in b] for b in bursts] == [["a", "b", "c"], ["d", "e"]]
    assert [[m["id"] for m in b] for b in be.group_bursts(rows, gap_seconds=3.0)] == [["a", "b"], ["c"], ["d", "e"]]
    assert be.burst_id_of(bursts[0]) == f"{DEP}:a"


def test_group_bursts_orders_by_time_then_file_name():
    rows = [_media("late", _ts(2)), _media("early", _ts(0)), _media("z", _ts(0), name="b"), _media("y", _ts(0), name="a")]
    (burst,) = be.group_bursts(rows)
    assert [m["id"] for m in burst] == ["y", "z", "early", "late"]


def test_group_bursts_never_spans_folder_or_deployment():
    rows = [
        _media("a", _ts(0), folder="MEDIA/A"),
        _media("b", _ts(1), folder="MEDIA/B"),  # same second, other folder
        _media("c", _ts(1), folder="MEDIA/A", dep=DEP2),  # other deployment
        _media("d", _ts(2), folder="MEDIA/A"),
    ]
    grouped = sorted([m["id"] for m in b] for b in be.group_bursts(rows))
    assert grouped == [["a", "d"], ["b"], ["c"]]


def test_group_bursts_prefers_the_firmware_sequence_tag_over_the_gap():
    rows = [
        _media("x2", _ts(0), exif={"trigger_id": "T7", "frame_index": 2}),
        _media("x1", _ts(40), exif={"trigger_id": "T7", "frame_index": 1}),  # 40 s apart, same tag: one burst, tag order
        _media("y", _ts(1), exif={"trigger_id": "T8"}),  # within 3 s of x2 but a different tag
        _media("z", _ts(2)),  # untagged: never merged with a tagged frame
    ]
    bursts = {be.burst_id_of(b): [m["id"] for m in b] for b in be.group_bursts(rows)}
    assert bursts == {f"{DEP}:T7": ["x1", "x2"], f"{DEP}:T8": ["y"], f"{DEP}:z": ["z"]}
    tagged = next(b for b in be.group_bursts(rows) if len(b) == 2)
    assert [be.burst_index_of(tagged, i) for i in range(2)] == [0, 1]  # the tag's 1-based frame_index, made 0-based
    gap = [{"exif_metadata": {"trigger_id": "T", "frame_index": 1}}, {"exif_metadata": {"trigger_id": "T", "frame_index": 3}}]
    assert [be.burst_index_of(gap, i) for i in range(2)] == [0, 2]  # frame 2 was lost: the tag wins over position
    assert be.sequence_tag({"exif_metadata": {"trigger_id": "T7", "frame_index": 2}}) == ("T7", 2)
    assert be.sequence_tag({"exif_metadata": {"trigger_id": ""}}) == (None, None)
    assert be.sequence_tag({"exif_metadata": None}) == (None, None)
    assert be.sequence_tag({}) == (None, None)


def test_group_bursts_frames_without_timestamp_are_singletons():
    rows = [_media("a", _ts(0)), _media("n1"), _media("n2"), _media("b", _ts(1))]
    bursts = be.group_bursts(rows)
    assert [[m["id"] for m in b] for b in bursts] == [["a", "b"], ["n1"], ["n2"]]
    assert be.burst_index_of(bursts[0], 1) == 1


def test_media_registry_group_bursts_is_the_same_grouper():
    from app.domain.media_registry import group_bursts as registry_group_bursts

    rows = [_media("a", _ts(0)), _media("b", _ts(3)), _media("c", _ts(30))]
    assert registry_group_bursts(rows, 10.0) == be.group_bursts(rows, 10.0)


def test_folder_of_handles_providers_and_backslashes():
    assert be.folder_of("gdrive://MEDIA/00000000/IMAGES.000/A0000010.JPG") == "gdrive://MEDIA/00000000/IMAGES.000"
    assert be.folder_of("C:\\photos\\x\\1.jpg") == "C:/photos/x"
    assert be.folder_of(None) == ""


# ── Signals ──────────────────────────────────────────────────────────


def _obs(mid, kind, version, conf=None, comment=None, origin="cloud", source="ai", prob=None, stamp="t", label=None):
    return {
        "media_id": mid,
        "observation_type": kind,
        "source_type": source,
        "ai_origin": origin,
        "source_model_version": version,
        "classified_by": version,
        "confidence": conf,
        "classification_probability": prob,
        "classification_timestamp": stamp,
        "observation_comments": comment,
        "vernacular_name": label,
    }


def test_frame_signals_reads_every_writer_and_the_gemini_comment():
    obs = [
        _obs("m", "animal", SN, conf=0.31),
        _obs("m", "human", SN, conf=0.62),
        _obs("m", "animal", GEM, comment="visibility=obscured; size=small; conditions=night_ir | a rat"),
        _obs("m", "animal", "20V1", origin="edge", prob=0.87, label="rat"),
        _obs("m", "animal", "evidence_fusion_v1", source="consensus", origin=None, conf=0.9),  # ignored: not an input
        _obs("m", "animal", "human", source="human", origin=None),  # ignored
    ]
    s = be.frame_signals(
        {"id": "m", "deployment_id": DEP},
        obs,
        speciesnet_max_conf=0.62,
        confidence_threshold=0.2,
        motion_frac=0.01,
        burst_id="b",
        burst_index=1,
        burst_len=3,
        burst_animal_count=1,
    )
    assert s["speciesnet_presence"] == 1.0 and s["speciesnet_type"] == "human"  # the best-confidence kept type
    assert s["speciesnet_max_conf"] == 0.62 and s["near_threshold"] == 1.0
    assert s["gemini_presence"] == 0.8 and s["gemini_visibility"] == "obscured"
    assert s["edge_presence"] == 1.0 and s["edge_score"] == 0.87 and s["edge_label"] == "rat"
    assert s["motion_frac"] == 0.01 and s["motion"] == pytest.approx(0.5) and s["camera_shift"] is False
    assert (s["burst_id"], s["burst_index"], s["burst_len"], s["burst_animal_count"], s["neighbour_animal"]) == ("b", 1, 3, 1, 1.0)


def test_frame_signals_absent_means_none_never_zero():
    s = be.frame_signals({"id": "m", "deployment_id": DEP, "exif_metadata": {}}, [])
    assert s["speciesnet_presence"] is None and s["speciesnet_type"] is None and s["near_threshold"] is None
    assert s["gemini_presence"] is None and s["gemini_visibility"] is None
    assert s["motion_frac"] is None and s["motion"] is None
    assert s["edge_presence"] is None and s["edge_score"] is None
    assert s["neighbour_animal"] is None and s["burst_len"] == 1 and s["burst_animal_count"] == 0
    assert s["burst_id"] == f"{DEP}:m"
    # Blank rows are 0, not absent; a burst neighbour that is blank is 0, not absent.
    blank = be.frame_signals(
        {"id": "m", "deployment_id": DEP}, [_obs("m", "blank", SN), _obs("m", "blank", GEM, conf=0.95)], burst_len=2, burst_animal_count=0
    )
    assert blank["speciesnet_presence"] == 0.0 and blank["speciesnet_type"] == "blank"
    assert blank["gemini_presence"] == 0.0 and blank["gemini_visibility"] is None and blank["neighbour_animal"] == 0.0
    # A v1 Gemini animal row (bare description) counts 1.0; v2 labels weight it.
    assert be.frame_signals({"id": "m"}, [_obs("m", "animal", GEM, conf=1.0, comment="a rat")])["gemini_presence"] == 1.0
    assert be.frame_signals({"id": "m"}, [_obs("m", "animal", GEM, comment="visibility=clear | x")])["gemini_presence"] == 1.0
    assert be.frame_signals({"id": "m"}, [_obs("m", "animal", GEM, comment="visibility=partial | x")])["gemini_presence"] == 1.0
    # The latest Gemini row wins when a re-run left two.
    two = [_obs("m", "blank", GEM, stamp="2026-01-01"), _obs("m", "animal", GEM, comment="visibility=obscured | x", stamp="2026-02-01")]
    assert be.frame_signals({"id": "m"}, two)["gemini_presence"] == 0.8


def test_edge_signals_zero_when_nn_scores_present_but_none_cleared():
    exif = {"user_comment_fields": {"rat": "12%", "not rat": "88%", "Batt": "87"}}
    assert be.edge_signals([], exif) == (0.0, None, None)
    assert be.edge_signals([], {"user_comment_fields": {"note": "hello"}}) == (None, None, None)
    assert be.edge_signals([], None) == (None, None, None)
    assert be.edge_signals([_obs("m", "animal", "20V1", origin="edge", prob=0.9, label="rat")], exif) == (1.0, 0.9, "rat")


def test_edge_signals_ignore_person_and_vehicle_rows():
    # A type class (#135) writes a human or vehicle row; presence means an animal, so it does not count.
    exif = {"user_comment_fields": {"person": "91%", "no person": "9%"}}
    person = _obs("m", "human", "PD1", origin="edge", prob=0.91, label=None)
    vehicle = _obs("m", "vehicle", "PD1", origin="edge", prob=0.8, label=None)
    assert be.edge_signals([person], exif) == (0.0, None, None)
    assert be.edge_signals([person, vehicle], None) == (None, None, None)
    rat = _obs("m", "animal", "20V1", origin="edge", prob=0.7, label="rat")
    assert be.edge_signals([person, rat], exif) == (1.0, 0.7, "rat")


def test_motion_and_near_threshold_inputs():
    assert be.motion_input(None) == (None, False)
    assert be.motion_input(0.012) == (pytest.approx(0.6), False)
    assert be.motion_input(0.031) == (1.0, False)
    assert be.motion_input(0.0) == (0.0, False)
    assert be.motion_input(0.7) == (0.0, True)  # camera shift: zero and flagged
    assert be.near_threshold_input(None) is None
    assert be.near_threshold_input(0.14, 0.2) == pytest.approx(0.7)
    assert be.near_threshold_input(0.71, 0.2) == 1.0
    assert be.near_threshold_input(0.5, 0.0) is None
    assert be.gemini_presence_input(None) is None
    assert be.gemini_presence_input((False, "clear")) == 0.0


# ── Score ────────────────────────────────────────────────────────────


def test_evidence_score_weights_v1_and_absent_signals():
    assert be.WEIGHTS_V1 == {
        "speciesnet_presence": 0.50,
        "gemini_presence": 0.50,
        "neighbour_animal": 0.25,
        "motion": 0.15,
        "edge_presence": 0.15,
        "near_threshold": 0.10,
    }
    assert (be.THRESHOLD_V1, be.SUSPICIOUS_V1, be.WEIGHTS_VERSION) == (0.5, 0.25, "v1")
    score, c = be.evidence_score({"speciesnet_presence": 1.0})
    assert score == 0.5 and c["speciesnet_presence"] == 0.5 and c["gemini_presence"] is None  # absent adds nothing
    assert be.evidence_score({"gemini_presence": 0.8})[0] == pytest.approx(0.4)  # obscured needs one cheap signal
    assert be.evidence_score({"gemini_presence": 0.8, "edge_presence": 1.0})[0] == pytest.approx(0.55)
    assert be.evidence_score({"neighbour_animal": 1.0})[0] == 0.25  # suspicious, never flips alone
    assert be.evidence_score({"neighbour_animal": 1.0, "motion": 1.0})[0] == pytest.approx(0.4)
    assert be.evidence_score({"neighbour_animal": 1.0, "motion": 1.0, "near_threshold": 1.0})[0] == 0.5
    assert be.evidence_score({k: 1.0 for k in be.WEIGHTS_V1})[0] == 1.0  # clamped
    assert be.evidence_score({})[0] == 0.0
    assert be.evidence_score({"speciesnet_presence": None, "burst_id": "x"})[0] == 0.0


def test_consensus_type_band_and_audit_line():
    assert be.consensus_type(0.5) == "animal" and be.consensus_type(0.49) == "blank"
    assert be.consensus_type(0.1, "human") == "human"  # SpeciesNet's kept type wins
    assert be.consensus_type(0.9, "blank") == "animal" and be.consensus_type(0.9, None) == "animal"
    assert be.band_of(0.5) == "animal" and be.band_of(0.25) == "suspicious" and be.band_of(0.249) == "confirmed_blank"
    signals = {
        "speciesnet_presence": 0.0,
        "gemini_presence": 1.0,
        "neighbour_animal": 1.0,
        "motion": 0.6,
        "edge_presence": None,
        "near_threshold": 0.7,
    }
    assert (
        be.audit_line(0.91, 0.5, signals)
        == "evidence_fusion_v1 score=0.91 threshold=0.50 speciesnet=0 gemini=1 neighbour=1 motion=0.60 edge=absent near=0.70"
    )
    assert be.audit_line(0.91, 0.5, signals, cutoffs="det=0.20 vehicle=dropped").endswith("near=0.70 det=0.20 vehicle=dropped")


def test_six_frame_burst_worked_example_section_6_3():
    """Report section 6.3: only frame 2 flips to animal (0.91); 1, 5, 6 are suspicious blanks; 3 and 4 are 1.0."""
    burst = [_media(f"f{i}", _ts(i)) for i in range(1, 7)]
    obs = {m["id"]: [_obs(m["id"], "blank", SN), _obs(m["id"], "blank", GEM, comment="visibility=none | soil")] for m in burst}
    obs["f2"] = [_obs("f2", "blank", SN), _obs("f2", "animal", GEM, comment="visibility=partial; size=small | rat")]
    obs["f3"] = [_obs("f3", "animal", SN, conf=0.71), _obs("f3", "animal", GEM, comment="visibility=clear | rat")]
    obs["f4"] = [_obs("f4", "animal", SN, conf=0.66), _obs("f4", "animal", GEM, comment="visibility=clear | rat")]
    max_conf = {"f1": 0.03, "f2": 0.14, "f3": 0.71, "f4": 0.66, "f5": 0.17, "f6": 0.02}
    motion = [0.000, 0.012, 0.031, 0.028, 0.009, 0.001]
    signals = be.burst_signals(burst, obs, motion, max_conf, 0.2)
    scores = [be.evidence_score(s)[0] for s in signals]
    assert scores == pytest.approx([0.265, 0.91, 1.0, 1.0, 0.4025, 0.2675], abs=0.0005)
    assert [be.consensus_type(sc, s["speciesnet_type"]) for sc, s in zip(scores, signals)] == [
        "blank",
        "animal",
        "animal",
        "animal",
        "blank",
        "blank",
    ]
    assert [be.band_of(sc) for sc in scores] == ["suspicious", "animal", "animal", "animal", "suspicious", "suspicious"]
    assert [s["near_threshold"] for s in signals] == pytest.approx([0.15, 0.70, 1.0, 1.0, 0.85, 0.10])
    assert [s["motion"] for s in signals] == pytest.approx([0.0, 0.6, 1.0, 1.0, 0.45, 0.05])
    assert [s["gemini_presence"] for s in signals] == [0.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    assert all(s["edge_presence"] is None for s in signals)  # no edge model: absent, not 0
    assert [s["burst_index"] for s in signals] == [0, 1, 2, 3, 4, 5]
    assert [s["burst_animal_count"] for s in signals] == [3, 2, 2, 2, 3, 3]  # other frames with presence
    assert all(s["neighbour_animal"] == 1.0 and s["burst_len"] == 6 and s["burst_id"] == f"{DEP}:f1" for s in signals)


def test_burst_signals_singleton_has_no_motion_or_neighbour_and_validates_lengths():
    (s,) = be.burst_signals([_media("a", _ts(0))], {}, [0.9])
    assert s["motion_frac"] is None and s["motion"] is None and s["neighbour_animal"] is None
    with pytest.raises(ValueError):
        be.burst_signals([_media("a", _ts(0)), _media("b", _ts(1))], {}, [0.1])
    pair = be.burst_signals([_media("a", _ts(0)), _media("b", _ts(1))], {"b": [_obs("b", "animal", SN, conf=0.9)]}, [None, 0.05])
    assert pair[0]["neighbour_animal"] == 1.0 and pair[0]["burst_animal_count"] == 1 and pair[0]["motion"] is None
    assert pair[1]["neighbour_animal"] == 0.0 and pair[1]["burst_animal_count"] == 0 and pair[1]["motion"] == 1.0


# ── Motion fractions ─────────────────────────────────────────────────


def _frame(square: bool = False, size=(320, 240)) -> Image.Image:
    img = Image.new("RGB", size, (40, 40, 40))
    if square:
        ImageDraw.Draw(img).rectangle((100, 80, 180, 140), fill=(220, 220, 220))  # 80x60 of 320x240 = 6.25%
    return img


def test_compute_motion_fractions_reference_frame_and_still_frame():
    fracs = compute_motion_fractions([_frame(), _frame(square=True), _frame(square=True), _frame()])
    assert fracs[0] == pytest.approx(0.0625, abs=0.005)  # the reference differenced against frame 2
    assert fracs[1] == pytest.approx(0.0625, abs=0.005) and fracs[2] == pytest.approx(0.0625, abs=0.005)
    assert fracs[3] == 0.0  # identical to the reference: no carry-forward
    assert compute_motion_fractions([_frame()]) == [0.0]
    assert compute_motion_fractions([None, _frame(), None]) == [0.0, 0.0, 0.0]
    assert compute_motion_fractions([_frame(), None, _frame(square=True)])[1] == 0.0
