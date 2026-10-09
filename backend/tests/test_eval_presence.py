# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""eval_presence.py: metrics on a tiny synthetic CSV, dry-run estimates and the result cache (no network)."""

from __future__ import annotations

import io
import json
import os
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
    v3 = [
        ev.FrameOutcome(path="p1", truth=None, predicted=False, has_person=True),
        ev.FrameOutcome(path="p2", truth=None, predicted=True, has_person=False),
    ]
    assert "single / m / v3: 1 of 2 answered, has_person on 1" in ev.person_line({("single", "m", "v3"): v3}, {"p1", "p2"})


def test_only_list_filters_frames_and_drops_empty_bursts(tmp_path):
    root = tmp_path / "export"
    only = tmp_path / "only.txt"
    only.write_text("# subset\nA/1.jpg\n\n" + str(root / "B" / "3.jpg") + "\n", encoding="utf-8")
    paths = ev.read_only_list(str(only), str(root))
    assert paths == {os.path.normpath(str(root / "A" / "1.jpg")), os.path.normpath(str(root / "B" / "3.jpg"))}
    frames = [
        ev.LabelledFrame(path=os.path.normpath(str(root / d / f"{i}.jpg")), burst_id=d, has_animal=True) for d, i in (("A", 1), ("A", 2), ("C", 4))
    ]
    kept = ev.filter_only([frames[:2], frames[2:]], paths)
    assert [[f.path for f in b] for b in kept] == [[frames[0].path]]  # C's burst had no listed frame
    assert ev.filter_only([frames], None) == [frames]


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
    assert "**single / m / prompt v2**" in table and "| light | night_ir | 1 | 1 | too few |  | 1 | n/a |" in table
    assert "| light | night_ir | 1 | 1 | 0.0% | 0.0 to 79.3% | 1 | n/a |" in ev.render_strata_markdown({("single", "m", "v2"): per}, floor=1)
    assert "light_source" not in per  # counted by the source line, not a table row


def test_wilson_interval_matches_the_published_values():
    assert ev.wilson_interval(0, 0) is None
    lo, hi = ev.wilson_interval(8, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.4902, 0.9433)  # Newcombe 1998, table I
    lo, hi = ev.wilson_interval(30, 30)
    assert hi == 1.0 and lo == pytest.approx(0.8865, abs=1e-4)  # a perfect stratum still has a lower bound
    assert ev.wilson_interval(0, 30)[0] == 0.0


def test_strata_floor_reports_too_few_below_30_animal_frames():
    def stratum(n_animal, n_missed, n_empty=0):
        outs = [_outcome(True, i >= n_missed) for i in range(n_animal)] + [_outcome(False, False)] * n_empty
        return ev.compute_metrics(outs)

    per = {("single", "m", "v1"): {"size": {"small": stratum(30, 3, 30), "tiny": stratum(29, 0, 29), "none": stratum(0, 0, 1)}}}
    table = ev.render_strata_markdown(per)
    assert "| size | small | 60 | 30 | 90.0% | 74.4 to 96.5% | 3 | 100.0% |" in table
    assert "| size | tiny | 58 | 29 | too few |  | 0 | too few |" in table
    assert "| size | none | 1 | 0 | n/a |  | 0 | too few |" in table
    assert ev.MIN_STRATUM_ANIMAL_FRAMES == 30


def test_size_and_border_from_a_box_and_who_decides():
    assert ev.size_class((0.0, 0.0, 0.1, 0.1)) == "tiny"  # 1% of the frame
    assert ev.size_class((0.0, 0.0, 0.2, 0.25)) == "small"  # 5%
    assert ev.size_class((0.0, 0.0, 0.5, 0.5)) == "medium"  # 25%
    assert ev.size_class((0.0, 0.0, 0.6, 0.6)) == "large"  # 36%
    assert ev.on_border((0.01, 0.4, 0.1, 0.1)) and ev.on_border((0.5, 0.5, 0.49, 0.2))
    assert not ev.on_border((0.3, 0.3, 0.2, 0.2))
    plain = ev.LabelledFrame(path="a", burst_id="a", has_animal=True)
    human = ev.LabelledFrame(path="a", burst_id="a", has_animal=True, animal_size="large", visibility="border")
    box = (0.3, 0.3, 0.05, 0.05)
    assert ev.size_of(human, box, "tiny") == ("large", "human")  # a human label overrides the automatic one
    assert ev.size_of(plain, box, "large") == ("tiny", "box")
    assert ev.size_of(plain, None, "small") == ("small", "gemini")
    assert ev.size_of(plain, None, "none") == ("unknown", "none")
    assert ev.border_of(human, box, "centre") == ("border", "human")
    assert ev.border_of(plain, box, "corner") == ("interior", "box")
    assert ev.border_of(plain, None, "edge") == ("border", "gemini")
    assert ev.border_of(plain, None, "centre") == ("interior", "gemini")
    assert ev.border_of(plain, None, None) == ("unknown", "none")


def test_deployment_from_exif():
    assert ev.deployment_of({"deployment_id": "AD4CA41F-01e0-4d6e-881c-c7c2e6264564"}) == "ad4ca41f"
    assert ev.deployment_of({"deployment_id": "00000000-0000-0000-0000-000000000000"}) == "no_id"
    assert ev.deployment_of({}) == "no_id"


def test_boxes_prefer_speciesnet_and_model_labels_skip_none():
    sn = [ev.FrameOutcome(path="a", truth=True, predicted=True, bbox=(0.1, 0.1, 0.1, 0.1)), ev.FrameOutcome(path="b", truth=True, predicted=False)]
    gem = [
        ev.FrameOutcome(path="a", truth=True, predicted=True, bbox=(0.5, 0.5, 0.4, 0.4), size="large", location="none"),
        ev.FrameOutcome(path="b", truth=True, predicted=True, bbox=(0.2, 0.2, 0.2, 0.2), size="small", location="edge"),
        ev.FrameOutcome(path="c", truth=True, predicted=False, bbox=(0.2, 0.2, 0.2, 0.2)),  # a box on a "no" is not used
    ]
    runs = {("single", "m", "v2"): gem, ("speciesnet", "speciesnet (local)"): sn}
    assert ev.boxes_of(runs) == {"a": (0.1, 0.1, 0.1, 0.1), "b": (0.2, 0.2, 0.2, 0.2)}
    assert ev.model_labels_of(runs) == {"a": {"size": "large"}, "b": {"size": "small", "location": "edge"}}


def test_strata_add_deployment_box_and_human_families(tmp_path):
    paths = []
    for name in ("a.jpg", "b.jpg", "e.jpg"):
        Image.new("RGB", (64, 48), (30, 120, 60)).save(tmp_path / name, format="JPEG")
        paths.append(str(tmp_path / name))
    frames = [
        ev.LabelledFrame(path=paths[0], burst_id="x", has_animal=True, visibility="camouflaged", distance="far", conditions=("night_ir", "rain")),
        ev.LabelledFrame(path=paths[1], burst_id="x", has_animal=True),
        ev.LabelledFrame(path=paths[2], burst_id="x", has_animal=False, conditions=("rain",)),
    ]
    strata = ev.strata_of(frames, [frames], boxes={paths[0]: (0.0, 0.0, 0.9, 0.9)}, model_labels={paths[1]: {"size": "tiny", "location": "centre"}})
    assert strata["light"][paths[0]] == "night_ir" and strata["light_source"][paths[0]] == "human"  # the label beats the colour ratio
    assert strata["light"][paths[1]] == "day"
    assert set(strata["deployment"].values()) == {"no_id"}
    assert strata["size"] == {paths[0]: "large", paths[1]: "tiny"}  # animal frames only
    assert strata["size_source"] == {paths[0]: "box", paths[1]: "gemini"}
    assert strata["border"] == {paths[0]: "border", paths[1]: "interior"}
    assert strata["visibility"] == {paths[0]: "camouflaged"} and strata["distance"] == {paths[0]: "far"}
    assert strata["conditions"] == {paths[0]: ("night_ir", "rain"), paths[2]: ("rain",)}
    assert ev.source_line(strata, "size", "Size") == "Size (2 frames): large 1, tiny 1; decided by box 1, gemini 1.\n"
    outcomes = [
        ev.FrameOutcome(path=paths[0], truth=True, predicted=False),
        ev.FrameOutcome(path=paths[1], truth=True, predicted=True),
        ev.FrameOutcome(path=paths[2], truth=False, predicted=False),
        ev.FrameOutcome(path="person.jpg", truth=None, predicted=True),  # in no stratum: left out, no "unknown" row
    ]
    per = ev.metrics_by_stratum(outcomes, strata)
    assert per["conditions"]["rain"].frames == 2 and per["conditions"]["night_ir"].recall == 0.0  # one frame, two conditions
    assert per["visibility"]["camouflaged"].fn == 1 and set(per["visibility"]) == {"camouflaged"}
    assert "unknown" not in per["light"] and not any(f.endswith("_source") for f in per)
    assert ev.human_line(frames) == "Human strata labelled on 2 animal frames: animal_size 0, visibility 1, distance 1, conditions 1.\n"
    # No human column anywhere: no human family at all.
    bare = ev.strata_of(frames[1:2], [frames[1:2]])
    assert not set(ev.HUMAN_FAMILIES) & set(bare)


def test_read_labels_reads_the_optional_strata_columns(tmp_path):
    p = tmp_path / "labels.csv"
    p.write_text(
        "path,burst_id,has_animal,label,labelled_by,labelled_at,notes,animal_size,visibility,conditions,distance\n"
        '/x/a.jpg,x/a,1,animal,v,t,,small,border,"night_ir,rain",close\n'
        "/x/b.jpg,x/a,1,animal,v,t,,,,fog;vegetation,\n",
        encoding="utf-8",
    )
    a, b = ev.read_labels(str(p))
    assert (a.animal_size, a.visibility, a.distance, a.conditions) == ("small", "border", "close", ("night_ir", "rain"))
    assert (b.animal_size, b.conditions) == ("", ("fog", "vegetation"))  # semicolons read as well


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
    assert calls == [("single", "v1"), ("single", "v1")]  # two frames, first run only
    assert [o.predicted for o in first] == [True, True] == [o.predicted for o in second]
    assert [(o.visibility, o.size, o.conditions, o.prompt_version) for o in second] == [("clear", "small", ["night_ir"], "v1")] * 2
    m = ev.compute_metrics(second)
    assert (m.tp, m.fp) == (1, 1) and m.tokens_per_frame == 310 and m.median_latency_s == 0.4
    assert len(open(cache, encoding="utf-8").read().splitlines()) == 2
    # A different prompt version never reuses those records; --max-calls caps the new calls.
    third = ev.run_variant(frames, "single", "gemini-3.1-flash-lite", dry_run=False, cache_path=cache, prompt_version="v2", max_calls=1)
    assert len(calls) == 3 and calls[-1] == ("single", "v2")
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


def test_speciesnet_dump_from_docker_is_rerooted_and_carries_the_top_animal_box(tmp_path):
    frames = _write_frames(tmp_path, 2)
    dump = tmp_path / "sn.json"
    detections = [
        {"type": "animal", "confidence": 0.3, "bbox": [0.1, 0.1, 0.2, 0.2]},
        {"type": "animal", "confidence": 0.8, "bbox": [0.5, 0.5, 0.3, 0.3]},
        {"type": "human", "confidence": 0.95, "bbox": [0.0, 0.0, 1.0, 1.0]},
        {"type": "animal", "confidence": 0.1, "bbox": [0.0, 0.0, 0.9, 0.9]},
    ]
    frames_json = {
        f"/photos/{Path(frames[0].path).name}": {"has_animal": True, "confidence": 0.8, "detections": detections},
        f"/photos/{Path(frames[1].path).name}": {"has_animal": False, "confidence": None, "detections": detections[-1:]},
    }
    dump.write_text(json.dumps({"model": "speciesnet-v4.0.1a", "threshold": 0.2, "frames": frames_json}), encoding="utf-8")
    outcomes = ev.speciesnet_outcomes(frames, str(dump), dump_root="/photos", root=str(tmp_path))
    assert [o.predicted for o in outcomes] == [True, False]
    assert outcomes[0].bbox == (0.5, 0.5, 0.3, 0.3) and outcomes[1].bbox is None  # below the dump's threshold
    assert [o.predicted for o in ev.speciesnet_outcomes(frames, str(dump))] == [None, None]  # not re-rooted: no match
    assert ev.rebase_path("/photos/a/b.jpg", "/photos", "C:/x") == os.path.normpath("C:/x/a/b.jpg")
    assert ev.rebase_path("/other/b.jpg", "/photos", "C:/x") == os.path.normpath("/other/b.jpg")


def test_cache_only_never_calls_the_api(tmp_path, monkeypatch):
    frames = _write_frames(tmp_path, 2)
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "path,burst_id,has_animal,label\n" + "".join(f"{f.path},b,{int(f.has_animal)},x\n" for f in frames),
        encoding="utf-8",
    )
    cache = tmp_path / "cache.jsonl"
    model = "gemini-3.1-flash-lite"
    record = {"key": ev._cache_key(model, "single", [frames[0]], "v1"), "verdicts": [{"has_animal": True, "bbox": [0.1, 0.1, 0.1, 0.1]}]}
    cache.write_text(json.dumps(record) + "\n", encoding="utf-8")
    before = cache.read_text(encoding="utf-8")
    monkeypatch.setattr(gp, "presence", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    out = tmp_path / "out.md"
    args = [str(labels), "--variants", "single", "--models", model, "--cache", str(cache), "--cache-only", "--out", str(out)]
    assert ev.main(args + ["--prompt-version", "v1"]) == 0
    assert cache.read_text(encoding="utf-8") == before  # nothing appended
    assert f"| single | {model} | v1 | 2 |" in out.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        ev.main([str(labels), "--variants", "single", "--cache-only"])


def test_jpeg_helper_roundtrip():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="JPEG")
    assert gp.prepare_single(buf.getvalue()).width == 4
