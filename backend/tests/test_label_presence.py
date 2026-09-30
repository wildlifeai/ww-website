# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""label_presence.py: burst grouping (EXIF, hex-name fallback) and CSV resumability. No UI."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from scripts import label_presence as lp


def _jpeg_with_exif(dt: str) -> bytes:
    exif = Image.Exif()
    exif[0x9003] = dt  # DateTimeOriginal
    buf = io.BytesIO()
    Image.new("RGB", (32, 24)).save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _frame(path, folder="/f", seconds=None, source="exif"):
    return lp.Frame(path=path, folder=folder, seconds=seconds, time_source=source)


def test_exif_seconds_reads_datetime_original():
    secs = lp.exif_seconds(_jpeg_with_exif("2026:06:20 00:03:32"))
    assert secs == datetime(2026, 6, 20, 0, 3, 32, tzinfo=timezone.utc).timestamp()
    assert lp.exif_seconds(b"BM\x00\x00") is None  # a BMP has no EXIF


def test_hex_name_seconds_follows_the_firmware_clock():
    # Two WW500 frames one second apart differ by 0x10 in their hex stem.
    assert lp.hex_name_seconds("A35D8D50.JPG") - lp.hex_name_seconds("A35D8D40.JPG") == 1.0
    assert lp.hex_name_seconds("IMG_0001.JPG") is None
    assert lp.hex_name_seconds("photo.jpg") is None


def test_make_frame_prefers_exif_then_hex_name(tmp_path):
    p = tmp_path / "A35D8D40.JPG"
    p.write_bytes(_jpeg_with_exif("2026:06:20 00:03:32"))
    f = lp.make_frame(str(p))
    assert f.time_source == "exif" and f.folder == str(tmp_path)
    bmp = tmp_path / "A35D8D50.BMP"
    Image.new("RGB", (8, 8)).save(bmp, format="BMP")
    g = lp.make_frame(str(bmp))
    assert g.time_source == "hex" and g.seconds == float(0xA35D8D50 >> 4)
    other = tmp_path / "holiday.png"
    Image.new("RGB", (8, 8)).save(other, format="PNG")
    assert lp.make_frame(str(other)).time_source == "none"


def test_group_bursts_by_gap_folder_and_clock():
    hex_clock = float(0xA35D8D40 >> 4)  # the firmware counter, about 1.7e8, far from any EXIF epoch second
    frames = [
        _frame("/f/a1.jpg", seconds=100.0),
        _frame("/f/a2.jpg", seconds=101.0),
        _frame("/f/a3.jpg", seconds=109.0),  # 8 s after a2: still the same trigger
        _frame("/f/b1.jpg", seconds=200.0),  # new trigger
        _frame("/g/c1.jpg", folder="/g", seconds=201.0),  # same time, other folder: never merged
        _frame("/f/h1.bmp", seconds=hex_clock, source="hex"),  # hex clock is not the EXIF clock
        _frame("/f/h2.bmp", seconds=hex_clock + 2.0, source="hex"),
        _frame("/f/n1.png", seconds=None, source="none"),  # no time: singleton
        _frame("/f/n2.png", seconds=None, source="none"),
    ]
    bursts = lp.group_bursts(frames, gap_seconds=10.0)
    grouped = sorted(tuple(Path(f.path).name for f in b.frames) for b in bursts)
    assert grouped == sorted([("a1.jpg", "a2.jpg", "a3.jpg"), ("b1.jpg",), ("c1.jpg",), ("h1.bmp", "h2.bmp"), ("n1.png",), ("n2.png",)])
    ids = {b.burst_id for b in bursts}
    assert "f/a1" in ids and "g/c1" in ids
    assert lp.DEFAULT_GAP_SECONDS == 10.0
    row = lp.media_row(frames[0])
    assert row["deployment_id"] == "/f" and row["file_path"] == "/f/a1.jpg" and row["timestamp"] == "1970-01-01T00:01:40+00:00"
    assert lp.media_row(frames[-1])["timestamp"] is None


def test_group_bursts_sorts_by_time_not_name():
    frames = [_frame("/f/z.jpg", seconds=100.0), _frame("/f/a.jpg", seconds=101.0)]
    (b,) = lp.group_bursts(frames, 10.0)
    assert [Path(f.path).name for f in b.frames] == ["z.jpg", "a.jpg"]
    assert b.burst_id == "f/z"


def test_csv_append_load_and_resume(tmp_path):
    csv_path = tmp_path / "labels.csv"
    burst = lp.Burst(burst_id="f/a1", frames=(_frame("/f/a1.jpg", seconds=1.0), _frame("/f/a2.jpg", seconds=2.0)))
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    lp.append_label(str(csv_path), lp.verdict_row(burst.frames[0], burst, "a", "charles", "eye shine", now))
    lp.append_label(str(csv_path), lp.verdict_row(burst.frames[1], burst, "u", "charles", "", now))

    text = csv_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(lp.CSV_COLUMNS)
    assert "\r" not in text  # LF only

    labelled = lp.load_labels(str(csv_path))
    assert set(labelled) == {lp.os.path.normpath("/f/a1.jpg"), lp.os.path.normpath("/f/a2.jpg")}
    assert labelled[lp.os.path.normpath("/f/a1.jpg")]["has_animal"] == "1"
    assert labelled[lp.os.path.normpath("/f/a2.jpg")]["has_animal"] == ""  # unsure
    assert labelled[lp.os.path.normpath("/f/a2.jpg")]["label"] == "unsure"

    # Resume: nothing pending once both are labelled; relabelling appends and the last row wins.
    assert lp.pending([burst], labelled) == []
    lp.append_label(str(csv_path), lp.verdict_row(burst.frames[1], burst, "e", "charles", "", now))
    again = lp.load_labels(str(csv_path))
    assert again[lp.os.path.normpath("/f/a2.jpg")]["label"] == "empty"
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 4  # header + 3 rows


def test_person_verdict_is_neither_animal_nor_empty():
    burst = lp.Burst(burst_id="f/p", frames=(_frame("/f/p.jpg", seconds=1.0),))
    row = lp.verdict_row(burst.frames[0], burst, "p", "victor")
    assert (row["label"], row["has_animal"]) == ("person", "")


def test_apply_stratum_number_keys_are_optional_and_toggle():
    from app.services import gemini_presence as gp

    row = {"path": "/f/a.jpg", "label": "animal", "has_animal": "1"}
    r = lp.apply_stratum(row, "1")
    assert r["animal_size"] == "tiny" and row.get("animal_size") is None  # pure: the input is untouched
    r = lp.apply_stratum(r, "3")
    assert r["animal_size"] == "medium"
    assert lp.apply_stratum(r, "3")["animal_size"] == ""  # same key again clears it
    r = lp.apply_stratum(lp.apply_stratum(r, "5"), "8")
    assert r["visibility"] == "clear" and r["conditions"] == "night_ir"
    r = lp.apply_stratum(r, "9")
    assert r["conditions"] == "night_ir,low_light"
    assert lp.apply_stratum(r, "8")["conditions"] == "low_light"  # conditions toggle
    assert lp.apply_stratum(r, "x") == r
    assert lp.strata_summary(r) == "medium, clear, night_ir,low_light"
    assert lp.strata_summary({}) == "no strata"
    # The labeller's vocabulary is the prompt's, so labels and model answers compare directly.
    for column, value in lp.STRATA_KEYS.values():
        allowed = {"animal_size": gp.SIZE_VALUES, "visibility": gp.VISIBILITY_VALUES, "conditions": gp.VISUAL_CONDITIONS}[column]
        assert value in allowed


def test_legacy_csv_is_widened_in_place_and_stays_readable(tmp_path):
    csv_path = tmp_path / "labels.csv"
    legacy_header = ",".join(lp.LEGACY_CSV_COLUMNS)
    csv_path.write_text(f"{legacy_header}\n/f/a.jpg,f/a,1,animal,victor,t,\n/f/b.jpg,f/a,0,empty,victor,t,note\n", encoding="utf-8")
    assert lp.csv_columns_of(str(csv_path)) == lp.LEGACY_CSV_COLUMNS
    assert lp.ensure_columns(str(csv_path)) is True
    assert lp.ensure_columns(str(csv_path)) is False  # already widened
    text = csv_path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(lp.CSV_COLUMNS) and "\r" not in text
    assert text.splitlines()[2] == "/f/b.jpg,f/a,0,empty,victor,t,note,,,"
    labels = lp.load_labels(str(csv_path))
    assert labels[lp.os.path.normpath("/f/a.jpg")]["has_animal"] == "1" and labels[lp.os.path.normpath("/f/a.jpg")]["animal_size"] == ""
    # A strata row appended afterwards lines up with the widened header, and the last row wins.
    burst = lp.Burst("f/a", (_frame("/f/a.jpg", seconds=1.0),))
    row = lp.apply_stratum(lp.verdict_row(burst.frames[0], burst, "a", "victor", "", datetime(2026, 9, 29, tzinfo=timezone.utc)), "1")
    lp.append_label(str(csv_path), row)
    again = lp.load_labels(str(csv_path))[lp.os.path.normpath("/f/a.jpg")]
    assert again["animal_size"] == "tiny" and again["visibility"] == "" and again["label"] == "animal"
    # Appending to a legacy file that was never opened by the UI widens it first.
    other = tmp_path / "old.csv"
    other.write_text(f"{legacy_header}\n/f/z.jpg,f/z,0,empty,v,t,\n", encoding="utf-8")
    lp.append_label(str(other), row)
    assert other.read_text(encoding="utf-8").splitlines()[0] == ",".join(lp.CSV_COLUMNS)
    assert lp.load_labels(str(other))[lp.os.path.normpath("/f/z.jpg")]["label"] == "empty"
    assert lp.csv_columns_of(str(tmp_path / "missing.csv")) is None


def test_pending_keeps_burst_order_and_skips_labelled():
    b1 = lp.Burst("f/a", (_frame("/f/a.jpg", seconds=1.0), _frame("/f/b.jpg", seconds=2.0)))
    b2 = lp.Burst("f/c", (_frame("/f/c.jpg", seconds=50.0),))
    todo = lp.pending([b1, b2], {lp.os.path.normpath("/f/a.jpg"): {}})
    assert [(Path(f.path).name, b.burst_id, i) for f, b, i in todo] == [("b.jpg", "f/a", 1), ("c.jpg", "f/c", 0)]


def test_collect_frames_recurses_and_filters_extensions(tmp_path):
    (tmp_path / "MEDIA" / "00000000" / "IMAGES.000").mkdir(parents=True)
    for name in ("A0000010.JPG", "A0000020.BMP", "notes.txt"):
        p = tmp_path / "MEDIA" / "00000000" / "IMAGES.000" / name
        if name.endswith(".txt"):
            p.write_text("x")
        else:
            Image.new("RGB", (8, 8)).save(p, format="BMP" if name.endswith("BMP") else "JPEG")
    frames = lp.collect_frames([str(tmp_path)])
    assert [Path(f.path).name for f in frames] == ["A0000010.JPG", "A0000020.BMP"]


@pytest.mark.skipif(
    not Path("C:/Users/ww/ww-website/test-fixtures/camera-trap/sdcard/dev-sdcard/MEDIA/7785FABB").exists(), reason="local fixture frames not present"
)
def test_real_fixture_frames_group_into_bursts():
    frames = lp.collect_frames(["C:/Users/ww/ww-website/test-fixtures/camera-trap/sdcard/dev-sdcard/MEDIA/7785FABB"])
    bursts = lp.group_bursts(frames, 10.0)
    assert frames and bursts
    assert all(f.time_source in ("exif", "hex") for f in frames)
