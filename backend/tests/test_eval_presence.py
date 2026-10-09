# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""eval_presence.py: metrics on a tiny synthetic CSV, dry-run estimates and the result cache (no network)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.services import gemini_presence as gp
from scripts import eval_presence as ev


def _outcome(truth, predicted, tokens=100.0, cost=0.001, latency=0.5):
    return ev.FrameOutcome(path="p", truth=truth, predicted=predicted, input_tokens=tokens, output_tokens=0.0, cost_usd=cost, latency_s=latency)


def test_compute_metrics_confusion_and_rates():
    outcomes = [
        _outcome(True, True),
        _outcome(True, True),
        _outcome(True, False),  # missed animal
        _outcome(False, False),
        _outcome(False, False),
        _outcome(False, False),
        _outcome(False, True),  # kept empty
        _outcome(None, True),  # unsure: excluded
    ]
    m = ev.compute_metrics(outcomes)
    assert (m.frames, m.animal_frames, m.empty_frames) == (7, 3, 4)
    assert (m.tp, m.fn, m.fp, m.tn) == (2, 1, 1, 3)
    assert m.recall == pytest.approx(2 / 3)
    assert m.false_negative_rate == pytest.approx(1 / 3)
    assert m.precision == pytest.approx(2 / 3)
    assert m.empty_removed == pytest.approx(3 / 4)
    assert m.tokens_per_frame == 100.0 and m.usd_per_frame == pytest.approx(0.001)
    assert m.usd_per_1000 == pytest.approx(1.0)
    assert m.median_latency_s == 0.5
    assert m.meets_t3 is False


def test_compute_metrics_t3_target_and_unanswered_frames():
    good = [_outcome(True, True)] * 199 + [_outcome(True, False)] + [_outcome(False, False)] * 90 + [_outcome(False, True)] * 10
    m = ev.compute_metrics(good)
    assert m.false_negative_rate == pytest.approx(0.005) and m.empty_removed == pytest.approx(0.9)
    assert m.meets_t3 is True
    # An unanswered frame is kept, never dropped: an animal stays a TP, an empty becomes an FP.
    m2 = ev.compute_metrics([_outcome(True, None), _outcome(False, None)])
    assert (m2.tp, m2.fp, m2.unanswered) == (1, 1, 2)
    assert ev.compute_metrics([]).recall is None


def _csv(tmp_path, rows):
    p = tmp_path / "labels.csv"
    lines = ["path,burst_id,has_animal,label,labelled_by,labelled_at,notes"] + [",".join(r) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_read_labels_last_row_wins(tmp_path):
    path = _csv(
        tmp_path,
        [
            ("/x/a1.jpg", "x/a1", "1", "animal", "v", "t", ""),
            ("/x/a2.jpg", "x/a1", "0", "empty", "v", "t", ""),
            ("/x/b1.jpg", "x/b1", "", "unsure", "v", "t", ""),
            ("/x/a2.jpg", "x/a1", "1", "animal", "v", "t", "relabelled"),
        ],
    )
    frames = ev.read_labels(path)
    assert len(frames) == 3
    by_name = {f.path.replace("\\", "/")[-6:]: f for f in frames}
    assert by_name["a2.jpg"].has_animal is True
    assert by_name["b1.jpg"].has_animal is None


def test_read_labels_resolves_relative_paths_and_keeps_the_person_label(tmp_path):
    path = _csv(
        tmp_path,
        [
            ("sub/a.jpg", "sub/a", "1", "animal", "v", "t", ""),
            ("sub/p.jpg", "sub/p", "", "person", "v", "t", ""),
        ],
    )
    frames = {Path(f.path).name: f for f in ev.read_labels(path, root=str(tmp_path / "root"))}
    assert Path(frames["a.jpg"].path) == tmp_path / "root" / "sub" / "a.jpg"
    assert frames["p.jpg"].has_animal is None and frames["p.jpg"].label == "person"
    assert Path(ev.read_labels(path)[0].path) == Path("sub") / "a.jpg"  # no root: left as written


def test_person_line_counts_person_frames_called_animal_and_skips_unanswered():
    outs = [
        ev.FrameOutcome(path="p1", truth=None, predicted=True),
        ev.FrameOutcome(path="p2", truth=None, predicted=False),
        ev.FrameOutcome(path="p3", truth=None, predicted=None),
        ev.FrameOutcome(path="a", truth=True, predicted=True),
    ]
    line = ev.person_line({("single", "m", "v2"): outs}, {"p1", "p2", "p3"})
    assert "Person frames (3" in line and "single / m / v2: 1 of 2 answered" in line
    assert ev.person_line({("single", "m", "v2"): outs}, set()) == ""
    assert ev.compute_metrics(outs).frames == 1  # person frames never enter the wildlife metrics


def _exif_jpeg(path, dt: str) -> None:
    exif = Image.Exif()
    exif[0x9003] = dt  # DateTimeOriginal
    Image.new("RGB", (32, 24)).save(path, format="JPEG", exif=exif)


def test_compute_bursts_uses_the_shared_grouper_and_limit_keeps_whole_bursts(tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    _exif_jpeg(tmp_path / "A" / "1.jpg", "2026:06:16 17:42:00")
    _exif_jpeg(tmp_path / "A" / "2.jpg", "2026:06:16 17:42:04")  # 4 s: one trigger at the 10 s default
    _exif_jpeg(tmp_path / "A" / "3.jpg", "2026:06:16 17:43:00")  # new trigger
    _exif_jpeg(tmp_path / "B" / "4.jpg", "2026:06:16 17:43:01")  # other folder: never merged
    frames = [
        ev.LabelledFrame(path=str(tmp_path / d / f"{i}.jpg"), burst_id="ignored", has_animal=True)
        for d, i in (("A", 1), ("A", 2), ("A", 3), ("B", 4))
    ]
    frames.append(ev.LabelledFrame(path=str(tmp_path / "missing.jpg"), burst_id="x", has_animal=False))  # unreadable: singleton
    bursts = ev.compute_bursts(frames)
    assert [[Path(f.path).name for f in b] for b in bursts] == [["1.jpg", "2.jpg"], ["3.jpg"], ["4.jpg"], ["missing.jpg"]]
    assert [[Path(f.path).name for f in b] for b in ev.compute_bursts(frames, gap_seconds=3.0)][:2] == [["1.jpg"], ["2.jpg"]]
    limited = ev.apply_limit(bursts, 1)  # the first burst has two frames: keep both
    assert [len(b) for b in limited] == [2] and ev.apply_limit(bursts, None) is bursts
    assert [len(b) for b in ev.apply_limit(bursts, 3)] == [2, 1]


def test_work_units_per_variant():
    frames = [ev.LabelledFrame(path=f"/b/{i}.jpg", burst_id="b", has_animal=True) for i in range(8)] + [
        ev.LabelledFrame(path="/c/1.jpg", burst_id="c", has_animal=False)
    ]
    bursts = [frames[:8], frames[8:]]
    assert [len(u) for u in ev.work_units(frames, "single")] == [1] * 9
    assert [len(u) for u in ev.work_units(frames, "batch")] == [1] * 9
    assert [len(u) for u in ev.work_units(frames, "contact_sheet", bursts)] == [gp.SHEET_MAX_CELLS, 8 - gp.SHEET_MAX_CELLS, 1]
    assert [len(u) for u in ev.work_units(frames, "contact_sheet")] == [1] * 9  # no readable EXIF: every frame a singleton


def test_render_markdown_has_one_row_per_variant_model_prompt_and_t3_column():
    m = ev.compute_metrics([_outcome(True, True), _outcome(False, False)])
    table = ev.render_markdown({("single", "gemini-3.1-flash-lite", "v2"): m, ("speciesnet", "speciesnet (local)"): m}, dry_run=False, n_unsure=2)
    assert "| Prompt |" in table
    assert "| single | gemini-3.1-flash-lite | v2 | 2 | 100.0% | 0.0% | 100.0% | 100.0% |" in table
    assert "| speciesnet | speciesnet (local) | n/a |" in table
    assert "yes |" in table and "2 frame(s) labelled unsure" in table
    dry = ev.render_markdown({("batch", "gemini-3.1-flash-lite", "v1"): m}, dry_run=True)
    assert "est." in dry and "no API call" in dry


def test_cache_key_carries_the_prompt_version_but_keeps_v1_keys_resumable():
    unit = [ev.LabelledFrame(path="/x/a.jpg", burst_id="b", has_animal=True)]
    v1 = json.loads(ev._cache_key("m", "single", unit, "v1"))
    v2 = json.loads(ev._cache_key("m", "single", unit, "v2"))
    assert "prompt" not in v1  # the 2026-09-28 cache was written before versioning existed
    assert v2["prompt"] == "v2" and v1 != v2
    assert ev._cache_key("m", "single", unit) == ev._cache_key("m", "single", unit, gp.PROMPT_VERSION)


def test_strata_light_burst_length_and_top_folder(tmp_path):
    grey = tmp_path / "MEDIA" / "night.jpg"
    colour = tmp_path / "Tommy" / "day.jpg"
    grey.parent.mkdir()
    colour.parent.mkdir()
    Image.new("RGB", (64, 48), (90, 90, 90)).save(grey, format="JPEG")
    Image.new("RGB", (64, 48), (30, 120, 60)).save(colour, format="JPEG")
    third = tmp_path / "Tommy" / "day2.jpg"
    Image.new("RGB", (64, 48), (30, 120, 60)).save(third, format="JPEG")
    frames = [
        ev.LabelledFrame(path=str(grey), burst_id="n", has_animal=True),
        ev.LabelledFrame(path=str(colour), burst_id="d", has_animal=True),
        ev.LabelledFrame(path=str(third), burst_id="d", has_animal=False),
    ]
    strata = ev.strata_of(frames, [frames[:1], frames[1:]])
    assert strata["light"] == {str(grey): "night_ir", str(colour): "day", str(third): "day"}
    assert set(strata["light_source"].values()) == {"greyscale"}  # no flash or exposure metadata
    assert strata["burst_len"] == {str(grey): "1", str(colour): "2", str(third): "2"}
    assert strata["folder"] == {str(grey): "MEDIA", str(colour): "Tommy", str(third): "Tommy"}
    assert ev.burst_len_bucket(3) == "3+" and ev.burst_len_bucket(7) == "3+"
    outcomes = [
        ev.FrameOutcome(path=str(grey), truth=True, predicted=False, visibility="none", size="none", location="none"),
        ev.FrameOutcome(path=str(colour), truth=True, predicted=True, visibility="partial", size="tiny", location="corner"),
        ev.FrameOutcome(path=str(third), truth=False, predicted=False, visibility="none", size="none", location="none"),
    ]
    per = ev.metrics_by_stratum(outcomes, strata)
    assert per["light"]["night_ir"].recall == 0.0 and per["light"]["day"].recall == 1.0
    assert per["burst_len"]["2"].empty_removed == 1.0
    assert per["gemini_size"]["tiny"].recall == 1.0 and per["gemini_size"]["none"].recall == 0.0  # the model's own label, animal frames only
    assert set(per["gemini_location"]) == {"corner", "none"}
    table = ev.render_strata_markdown({("single", "m", "v2"): per})
    assert "**single / m / prompt v2**" in table and "| light | night_ir | 1 | 1 | 0.0% | 1 | n/a |" in table


def _ww500_jpeg(maker_note=None, flash=None, rgb=(90, 90, 90)) -> bytes:
    """A frame with the WW500's EXIF: little-endian, Flash and MakerNote in the Exif sub-IFD."""
    exif = Image.Exif()
    exif.endian = "<"  # as the firmware writes it
    sub = exif.get_ifd(0x8769)
    if flash is not None:
        sub[0x9209] = flash
    if maker_note is not None:
        sub[0x927C] = maker_note
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), rgb).save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


DAY_NOTE = "376, 2, 65, 70, Y"  # Colorado, 18:15 local
DARK_NOTE = "376, 4, 192, 5, N"  # Colorado, 20:19 local: the AE spending digital gain


def test_light_flash_fired_is_night_ir_whatever_the_exposure():
    assert ev.light_of(_ww500_jpeg(DAY_NOTE, flash=1)) == ("night_ir", "flash")
    assert ev.light_of(_ww500_jpeg(DAY_NOTE + ", 0, 0, 2")) == ("night_ir", "flash")  # the MakerNote copy (IR = 2)
    # A flash that did not fire decides nothing: the flash can be switched off.
    assert ev.light_of(_ww500_jpeg(DARK_NOTE, flash=0)) == ("night_ir", "exposure")
    assert ev.light_of(_ww500_jpeg(DAY_NOTE, flash=0)) == ("day", "exposure")


def test_light_from_ww500_exposure_beats_the_greyscale_ratio():
    # Both frames are greyscale, as every HM0360 frame is; the exposure decides.
    assert ev.light_of(_ww500_jpeg(DAY_NOTE)) == ("day", "exposure")
    assert ev.light_of(_ww500_jpeg(DARK_NOTE)) == ("night_ir", "exposure")
    assert ev.exposure_of({"integration_lines": 376, "analog_gain": 4, "digital_gain": 65}) == pytest.approx(6110)  # brightest day
    assert ev.exposure_of({"integration_lines": 376, "analog_gain": 4, "digital_gain": 128}) == ev.LOW_LIGHT_MIN_EXPOSURE
    assert ev.exposure_of({}) is None
    assert ev.exposure_of({"integration_lines": 0, "analog_gain": 0, "digital_gain": 0}) is None  # no sensor read


def test_light_falls_back_to_the_greyscale_ratio_without_metadata():
    assert ev.light_of(_ww500_jpeg()) == ("night_ir", "greyscale")
    assert ev.light_of(_ww500_jpeg(rgb=(30, 120, 60))) == ("day", "greyscale")
    assert ev.light_of(_ww500_jpeg("Canon MakerNote blob", rgb=(30, 120, 60))) == ("day", "greyscale")


def test_strata_record_the_light_source_and_the_line_counts_them(tmp_path):
    paths = []
    for name, data in (("day.jpg", _ww500_jpeg(DAY_NOTE)), ("ir.jpg", _ww500_jpeg(DAY_NOTE, flash=1)), ("grey.jpg", _ww500_jpeg())):
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    frames = [ev.LabelledFrame(path=p, burst_id=p, has_animal=True) for p in paths + [str(tmp_path / "missing.jpg")]]
    strata = ev.strata_of(frames, [[f] for f in frames])
    assert list(strata["light_source"].values()) == ["exposure", "flash", "greyscale", "unreadable"]
    assert ev.light_line(strata) == ("Light (4 frames): day 1, night_ir 2, unknown 1; decided by exposure 1, flash 1, greyscale 1, unreadable 1.\n")


def test_dump_verdicts_writes_structured_fields(tmp_path):
    o = ev.FrameOutcome(path="/x/a.jpg", truth=True, predicted=True, visibility="partial", size="small", conditions=["night_ir"], prompt_version="v2")
    out = tmp_path / "v.json"
    ev.dump_verdicts({("single", "m", "v2"): [o]}, str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["single/m/v2"]["/x/a.jpg"]["animal_visibility"] == "partial"
    assert data["single/m/v2"]["/x/a.jpg"]["visual_conditions"] == ["night_ir"]


def _write_frames(tmp_path, n, burst="b"):
    frames = []
    for i in range(n):
        p = tmp_path / f"A000{i:04d}.JPG"
        Image.new("RGB", (640, 480), (i, i, i)).save(p, format="JPEG")
        frames.append(ev.LabelledFrame(path=str(p), burst_id=burst, has_animal=(i % 2 == 0)))
    return frames


def test_run_variant_dry_run_estimates_without_calling(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "_generate_content", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    frames = _write_frames(tmp_path, 4)
    single = ev.compute_metrics(ev.run_variant(frames, "single", "gemini-3.1-flash-lite", dry_run=True, cache_path=None))
    sheet = ev.compute_metrics(ev.run_variant(frames, "contact_sheet", "gemini-3.1-flash-lite", dry_run=True, cache_path=None))
    batch = ev.compute_metrics(ev.run_variant(frames, "batch", "gemini-3.1-flash-lite", dry_run=True, cache_path=None))
    assert single.frames == 4 and single.tokens_per_frame > 0 and single.usd_per_frame > 0
    assert sheet.tokens_per_frame < single.tokens_per_frame  # one image for four frames
    assert batch.usd_per_frame == pytest.approx(single.usd_per_frame / 2)


def test_run_variant_live_uses_cache_and_never_pays_twice(tmp_path, monkeypatch):
    frames = _write_frames(tmp_path, 2)
    calls = []

    def fake_presence(images, variant, model, **kw):
        calls.append((variant, kw.get("prompt_version")))
        return gp.PresenceResult(
            model=model,
            variant=variant,
            verdicts=[gp.PresenceVerdict(True, 0.85, "x", animal_visibility="clear", animal_size="small", visual_conditions=("night_ir",))],
            usage=gp.TokenUsage(280, 30, 0, 310),
            cost_usd=0.0001,
            latency_s=0.4,
            prompt_version=kw.get("prompt_version"),
        )

    monkeypatch.setattr(gp, "presence", fake_presence)
    cache = str(tmp_path / "cache.jsonl")
    first = ev.run_variant(frames, "single", "gemini-3.1-flash-lite", dry_run=False, cache_path=cache)
    second = ev.run_variant(frames, "single", "gemini-3.1-flash-lite", dry_run=False, cache_path=cache)
    assert calls == [("single", "v2"), ("single", "v2")]  # two frames, first run only
    assert [o.predicted for o in first] == [True, True] == [o.predicted for o in second]
    assert [(o.visibility, o.size, o.conditions, o.prompt_version) for o in second] == [("clear", "small", ["night_ir"], "v2")] * 2
    m = ev.compute_metrics(second)
    assert (m.tp, m.fp) == (1, 1) and m.tokens_per_frame == 310 and m.median_latency_s == 0.4
    assert len(open(cache, encoding="utf-8").read().splitlines()) == 2
    # A different prompt version never reuses those records; --max-calls caps the new calls.
    third = ev.run_variant(frames, "single", "gemini-3.1-flash-lite", dry_run=False, cache_path=cache, prompt_version="v1", max_calls=1)
    assert len(calls) == 3 and calls[-1] == ("single", "v1")
    assert [o.predicted for o in third] == [True, None]  # the uncalled frame stays unanswered


def test_run_variant_batch_goes_through_presence_batch(tmp_path, monkeypatch):
    frames = _write_frames(tmp_path, 3)

    def fake_batch(groups, variant, model, **kw):
        return [gp.PresenceResult(model=model, variant="batch", verdicts=[gp.PresenceVerdict(False, 0.8)], cost_usd=0.00005) for _ in groups]

    monkeypatch.setattr(gp, "presence_batch", fake_batch)
    outcomes = ev.run_variant(frames, "batch", "gemini-3.1-flash-lite", dry_run=False, cache_path=None)
    assert [o.predicted for o in outcomes] == [False, False, False]


def test_speciesnet_outcomes_from_dump(tmp_path):
    frames = _write_frames(tmp_path, 2)
    dump = tmp_path / "sn.json"
    dump.write_text(
        json.dumps({"model": "speciesnet-v4.0.1a", "frames": {frames[0].path: {"has_animal": True, "confidence": 0.9}}}), encoding="utf-8"
    )
    outcomes = ev.speciesnet_outcomes(frames, str(dump))
    assert outcomes[0].predicted is True and outcomes[0].confidence == 0.9
    assert outcomes[1].predicted is None  # frame missing from the dump


def test_speciesnet_box_rules_recompute_the_dump(tmp_path):
    """#285: the rules row comes from the dumped detections, not the dumped has_animal."""
    from app.domain.pipeline import DetectionCutoffs

    frames = _write_frames(tmp_path, 3)
    whole = [0.0, 0.0, 1.0, 1.0]
    dump = tmp_path / "sn.json"
    frames_json = {
        # an animal filling the frame at 0.4: dropped by the whole-frame rule
        frames[0].path: {"has_animal": True, "confidence": 0.4, "detections": [{"type": "animal", "confidence": 0.4, "bbox": whole}]},
        # a small animal beside a whole-frame vehicle: the animal stays
        frames[1].path: {
            "has_animal": True,
            "confidence": 0.3,
            "detections": [
                {"type": "vehicle", "confidence": 0.45, "bbox": whole},
                {"type": "animal", "confidence": 0.3, "bbox": [0.1, 0.1, 0.1, 0.1]},
            ],
        },
        # an older dump without detections is left as it was
        frames[2].path: {"has_animal": True, "confidence": 0.9},
    }
    dump.write_text(json.dumps({"model": "speciesnet-v4.0.1a", "threshold": 0.2, "frames": frames_json}), encoding="utf-8")
    cutoffs = DetectionCutoffs(whole_frame_area=0.9, whole_frame_min_confidence=0.5, drop_vehicles=True)
    outcomes = ev.speciesnet_outcomes(frames, str(dump), cutoffs)
    assert [(o.predicted, o.confidence) for o in outcomes] == [(False, None), (True, 0.3), (True, 0.9)]
    assert [o.predicted for o in ev.speciesnet_outcomes(frames, str(dump))] == [True, True, True]


def test_jpeg_helper_roundtrip():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="JPEG")
    assert gp.prepare_single(buf.getvalue()).width == 4
