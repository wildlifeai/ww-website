#!/usr/bin/env python3
# Copyright (c) 2026
# SPDX-License-Identifier: GPL-3.0-or-later
"""
label_presence.py
=================
Ground-truth labeller for the animal-presence evaluation: folders of camera-trap
frames in, one CSV of per-frame verdicts out.

Shows each frame in a small Tkinter window (stdlib on Windows Python, Pillow for
the image) and takes a one-key verdict:

    a   animal present          e   empty frame          u   unsure
    p   person (no animal): kept out of the wildlife metrics, reported on its own
    b   back one frame (a new verdict for a frame appends a row; the LAST row
        per path wins when the CSV is read back)
    q   quit (progress is already on disk, every verdict is appended and flushed)

Optional strata, keys pressed AFTER a verdict (they amend the frame just
labelled, which is shown in the status line; each press appends a row, last
row wins):

    1 tiny  2 small  3 medium  4 large                              animal_size
    5 clear 6 partial 7 obscured k camouflaged o border             visibility
    c close m mid f far n na                                        distance
    8 night_ir 9 low_light 0 motion_blur r rain g fog v vegetation
    l lens_obstruction                                              conditions (toggle)

Size, the first three visibility values and the conditions are the prompt v2
vocabulary (``services/gemini_presence.py``), so a labelled stratum can be
compared with the model's own answer; ``camouflaged``, ``border`` and
``distance`` are the human strata of the architecture report, section 10.

Frames are grouped into trigger bursts by the one grouper the pipeline uses
(``app.domain.burst_evidence.group_bursts``, ``--gap`` seconds, default
``BURST_GAP_SECONDS`` = 10): each frame becomes a media-like row whose
``deployment_id`` is its folder and whose ``timestamp`` is the EXIF
``DateTimeOriginal``, falling back to the firmware's 8.3 hex filename clock
(``(seconds << 4) + subsecond``) when a frame has no EXIF (the WW500's BMP
diagnostic frames). A burst never spans two folders. The CSV is resumable:
frames already labelled are skipped.

CSV columns: ``path, burst_id, has_animal, label, labelled_by, labelled_at, notes,
animal_size, visibility, conditions, distance`` (``has_animal`` is 1/0, blank for
``unsure`` and ``person``; ``path`` is absolute here, and may be made relative
for a committed copy, read back with ``eval_presence.py --root``; the last four
are optional and blank unless entered, ``conditions`` a comma list). A CSV
written before a strata column existed is widened in place (blank strata) the
first time it is opened for labelling; reading any shape works.

Usage (from ``backend/``, any Python with Pillow)::

    python scripts/label_presence.py --out labels.csv --by charles \\
        "C:/Users/ww/AE_photos_charles" "C:/Users/ww/sample photos" \\
        "C:/Users/ww/ww-website/test-fixtures/camera-trap/sdcard"

    python scripts/label_presence.py --out labels.csv --list <folders>   # bursts only, no UI

The frame folders are read only, never moved or modified.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Allow running as ``python scripts/label_presence.py`` from backend/ (app.* imports).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.burst_evidence import DEFAULT_GAP_SECONDS  # noqa: E402
from app.domain.burst_evidence import group_bursts as group_media_bursts  # noqa: E402
from app.domain.exif import parse_exif_from_bytes  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
LEGACY_CSV_COLUMNS = ("path", "burst_id", "has_animal", "label", "labelled_by", "labelled_at", "notes")
STRATA_COLUMNS = ("animal_size", "visibility", "conditions", "distance")
CSV_COLUMNS = LEGACY_CSV_COLUMNS + STRATA_COLUMNS
LABELS = {"a": "animal", "e": "empty", "u": "unsure", "p": "person"}
# Key -> (column, value). Size, visibility and distance are single-valued (a second
# press of the same key clears it); conditions accumulate as a comma list (toggle).
STRATA_KEYS = {
    "1": ("animal_size", "tiny"),
    "2": ("animal_size", "small"),
    "3": ("animal_size", "medium"),
    "4": ("animal_size", "large"),
    "5": ("visibility", "clear"),
    "6": ("visibility", "partial"),
    "7": ("visibility", "obscured"),
    "k": ("visibility", "camouflaged"),
    "o": ("visibility", "border"),
    "c": ("distance", "close"),
    "m": ("distance", "mid"),
    "f": ("distance", "far"),
    "n": ("distance", "na"),
    "8": ("conditions", "night_ir"),
    "9": ("conditions", "low_light"),
    "0": ("conditions", "motion_blur"),
    "r": ("conditions", "rain"),
    "g": ("conditions", "fog"),
    "v": ("conditions", "vegetation"),
    "l": ("conditions", "lens_obstruction"),
}


@dataclass(frozen=True)
class Frame:
    path: str  # absolute, normalised
    folder: str  # absolute parent folder
    seconds: Optional[float]  # capture time as seconds (EXIF epoch, or the hex-name clock)
    time_source: str  # 'exif' | 'hex' | 'none'


@dataclass(frozen=True)
class Burst:
    burst_id: str
    frames: tuple[Frame, ...]


# ── Pure helpers (unit-tested) ───────────────────────────────────────


def exif_seconds(data: bytes) -> Optional[float]:
    """EXIF DateTimeOriginal (else DateTime) of a JPEG as epoch seconds, or None."""
    if len(data) < 2 or data[:2] != b"\xff\xd8":
        return None
    exif = parse_exif_from_bytes(data)
    for key in ("Datetime_Original", "DateTime", "Datetime_Create"):
        raw = exif.get(key)
        if not raw:
            continue
        try:
            return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def hex_name_seconds(filename: str) -> Optional[float]:
    """The firmware 8.3 hex stem as seconds on its own clock (``value >> 4``), or None."""
    stem = Path(filename).stem
    if len(stem) != 8:
        return None
    try:
        return float(int(stem, 16) >> 4)
    except ValueError:
        return None


def make_frame(path: str, data: Optional[bytes] = None) -> Frame:
    """Build a Frame, reading the file for EXIF when ``data`` is not supplied."""
    abs_path = os.path.normpath(os.path.abspath(path))
    if data is None:
        with open(abs_path, "rb") as fh:
            data = fh.read(65536)  # EXIF sits in the first segments
    secs = exif_seconds(data)
    source = "exif"
    if secs is None:
        secs = hex_name_seconds(os.path.basename(abs_path))
        source = "hex" if secs is not None else "none"
    return Frame(path=abs_path, folder=os.path.dirname(abs_path), seconds=secs, time_source=source)


def collect_frames(folders: Iterable[str]) -> list[Frame]:
    """Every image under ``folders`` (recursive), as Frames, in path order."""
    paths: list[str] = []
    for folder in folders:
        root = Path(folder)
        if root.is_file():
            paths.append(str(root))
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                paths.append(str(p))
    return [make_frame(p) for p in sorted(paths)]


def media_row(frame: Frame) -> dict:
    """The media-like row the shared grouper takes: folder as deployment, the frame's clock as timestamp.

    The EXIF clock (epoch seconds) and the hex-name clock (the firmware's own
    counter, about 1.7e8) are never within a gap of each other, so frames on
    different clocks never merge.
    """
    stamp = None if frame.seconds is None else datetime.fromtimestamp(frame.seconds, tz=timezone.utc).isoformat()
    return {"id": frame.path, "deployment_id": frame.folder, "file_path": frame.path, "file_name": os.path.basename(frame.path), "timestamp": stamp}


def group_bursts(frames: Iterable[Frame], gap_seconds: float = DEFAULT_GAP_SECONDS) -> list[Burst]:
    """Trigger bursts of ``frames`` through ``app.domain.burst_evidence.group_bursts`` (no grouping logic here).

    The burst id is ``<folder name>/<first frame stem>`` so it is stable across
    runs, readable, and the same as in the CSVs labelled before 2026-09-29.
    """
    by_path = {f.path: f for f in frames}
    bursts: list[Burst] = []
    for rows in group_media_bursts([media_row(f) for f in by_path.values()], gap_seconds):
        members = tuple(by_path[r["id"]] for r in rows)
        first = members[0]
        bursts.append(Burst(burst_id=f"{os.path.basename(first.folder)}/{Path(first.path).stem}", frames=members))
    return bursts


def load_labels(csv_path: str) -> dict[str, dict]:
    """Rows keyed by path; when a path was labelled twice the LAST row wins."""
    out: dict[str, dict] = {}
    if not os.path.exists(csv_path):
        return out
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("path"):
                out[os.path.normpath(row["path"])] = row
    return out


def csv_columns_of(csv_path: str) -> Optional[tuple[str, ...]]:
    """The header of an existing, non-empty CSV, else None."""
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return tuple(next(csv.reader(fh), []))


def ensure_columns(csv_path: str) -> bool:
    """Widen a legacy CSV (no strata columns) to ``CSV_COLUMNS`` in place, rows kept, new cells blank.

    Returns True when the file was rewritten. A file that already has every
    column, or no file, is left alone. LF line endings, UTF-8.
    """
    header = csv_columns_of(csv_path)
    if header is None or all(c in header for c in CSV_COLUMNS):
        return False
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tmp = csv_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (row.get(k) or "") for k in CSV_COLUMNS})
    os.replace(tmp, csv_path)
    return True


def append_label(csv_path: str, row: dict) -> None:
    """Append one verdict row, writing the header when the file is new; flushed immediately.

    A legacy CSV is widened first so the strata columns line up with the header.
    """
    ensure_columns(csv_path)
    new = csv_columns_of(csv_path) is None
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({k: (row.get(k) or "") for k in CSV_COLUMNS})
        fh.flush()


def apply_stratum(row: dict, key: str) -> dict:
    """A copy of ``row`` with the key's stratum applied (pure; unknown keys return the row unchanged)."""
    if key not in STRATA_KEYS:
        return dict(row)
    column, value = STRATA_KEYS[key]
    out = dict(row)
    if column == "conditions":
        current = [c for c in (out.get("conditions") or "").split(",") if c]
        current = [c for c in current if c != value] if value in current else current + [value]
        out["conditions"] = ",".join(current)
    else:
        out[column] = "" if out.get(column) == value else value
    return out


def strata_summary(row: dict) -> str:
    parts = [row.get(c) for c in STRATA_COLUMNS if row.get(c)]
    return ", ".join(parts) if parts else "no strata"


def verdict_row(frame: Frame, burst: Burst, key: str, labelled_by: str, notes: str = "", now: Optional[datetime] = None) -> dict:
    """The CSV row for one key press (``a``/``e``/``u``/``p``)."""
    label = LABELS[key]
    has_animal = "" if label in ("unsure", "person") else ("1" if label == "animal" else "0")
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {
        "path": frame.path,
        "burst_id": burst.burst_id,
        "has_animal": has_animal,
        "label": label,
        "labelled_by": labelled_by,
        "labelled_at": stamp,
        "notes": notes,
    }


def pending(bursts: list[Burst], labelled: dict[str, dict]) -> list[tuple[Frame, Burst, int]]:
    """(frame, burst, position) for every frame not yet in the CSV, burst order preserved."""
    out = []
    for b in bursts:
        for i, f in enumerate(b.frames):
            if os.path.normpath(f.path) not in labelled:
                out.append((f, b, i))
    return out


# ── Tkinter UI ───────────────────────────────────────────────────────


def run_ui(bursts: list[Burst], csv_path: str, labelled_by: str, max_width: int = 1100) -> None:
    import tkinter as tk

    from PIL import Image, ImageTk

    queue = pending(bursts, load_labels(csv_path))
    total = sum(len(b.frames) for b in bursts)
    if not queue:
        print(f"Nothing to do: all {total} frames are labelled in {csv_path}")
        return

    root = tk.Tk()
    root.title("label_presence")
    header = tk.Label(root, font=("Segoe UI", 11), anchor="w", justify="left")
    header.pack(fill="x", padx=8, pady=(6, 0))
    canvas = tk.Label(root)
    canvas.pack(padx=8, pady=6)
    notes_var = tk.StringVar()
    notes_row = tk.Frame(root)
    notes_row.pack(fill="x", padx=8, pady=(0, 6))
    tk.Label(notes_row, text="notes:").pack(side="left")
    notes_entry = tk.Entry(notes_row, textvariable=notes_var, width=60)
    notes_entry.pack(side="left", fill="x", expand=True)
    status = tk.Label(root, text="", fg="#246", anchor="w", justify="left")
    status.pack(fill="x", padx=8)
    footer = tk.Label(
        root,
        text=(
            "a = animal    e = empty    u = unsure    p = person    b = back    q = quit\n"
            "after a verdict (optional): 1 tiny 2 small 3 medium 4 large | "
            "5 clear 6 partial 7 obscured k camouflaged o border | c close m mid f far n na\n"
            "conditions (toggle): 8 night_ir 9 low_light 0 motion_blur r rain g fog v vegetation l lens_obstruction"
        ),
        fg="#555",
        justify="left",
    )
    footer.pack(pady=(0, 6))

    state = {"i": 0, "photo": None, "last": None}  # last = the most recent verdict row, target of the strata keys

    def show() -> None:
        if state["i"] >= len(queue):
            print(f"Done: {total} frames labelled in {csv_path}")
            root.destroy()
            return
        frame, burst, pos = queue[state["i"]]
        img = Image.open(frame.path).convert("RGB")
        scale = min(1.0, max_width / img.width)
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        state["photo"] = ImageTk.PhotoImage(img)
        canvas.configure(image=state["photo"])
        done = total - (len(queue) - state["i"])
        header.configure(
            text=f"{done + 1}/{total}    burst {burst.burst_id} (frame {pos + 1} of {len(burst.frames)}, time from {frame.time_source})\n{frame.path}"
        )
        notes_var.set("")
        root.focus_set()

    def on_key(event) -> None:
        if event.widget is notes_entry and event.keysym != "Return":
            return
        key = event.char.lower() if event.char else ""
        if event.keysym == "Return":
            root.focus_set()
            return
        if key == "q":
            root.destroy()
            return
        if key == "b":
            state["i"] = max(0, state["i"] - 1)
            show()
            return
        if key in LABELS:
            frame, burst, _pos = queue[state["i"]]
            row = verdict_row(frame, burst, key, labelled_by, notes_var.get().strip())
            append_label(csv_path, row)
            state["last"] = row
            status.configure(text=f"previous: {row['label']} ({os.path.basename(row['path'])}), {strata_summary(row)}")
            state["i"] += 1
            show()
            return
        if key in STRATA_KEYS and state["last"] is not None:
            row = apply_stratum(state["last"], key)
            append_label(csv_path, row)
            state["last"] = row
            status.configure(text=f"previous: {row['label']} ({os.path.basename(row['path'])}), {strata_summary(row)}")

    root.bind("<Key>", on_key)
    show()
    root.mainloop()


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="+", help="folders (or files) of frames, read recursively")
    ap.add_argument("--out", required=True, help="labels CSV (appended to; resumable)")
    ap.add_argument("--by", default=os.environ.get("USERNAME") or os.environ.get("USER") or "unknown", help="labelled_by value")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP_SECONDS, help="max seconds between frames of one burst")
    ap.add_argument("--list", action="store_true", help="print the burst grouping and exit (no UI)")
    args = ap.parse_args(argv)

    frames = collect_frames(args.folders)
    bursts = group_bursts(frames, args.gap)
    if ensure_columns(args.out):
        print(f"{args.out}: added the strata columns {', '.join(STRATA_COLUMNS)} (existing rows kept)")
    labelled = load_labels(args.out)
    print(f"{len(frames)} frames in {len(bursts)} bursts; {len(labelled)} already labelled in {args.out}")
    if args.list:
        for b in bursts:
            print(f"{b.burst_id}  ({len(b.frames)} frames, time from {b.frames[0].time_source})")
            for f in b.frames:
                print(f"    {'done ' if f.path in labelled else '     '}{f.path}")
        return 0
    run_ui(bursts, args.out, args.by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
