# Draft issue for wildlifeai/Seeed_Grove_Vision_AI_Module_V2 (not filed)

Title: EXIF capture-sequence tag: trigger id and frame index within the burst

Labels: review-finding, exif, cross-repo

---

**What:** Write a private EXIF tag into every JPEG naming the trigger the frame belongs to and
its position in that trigger's burst, so the website groups bursts without timestamp gaps.

**Where:** `EPII_CM55M_APP_S/app/ww_projects/ww500_md/exif_builder.h` (`ExifTagID`,
`ExifInput_t`), `exif_builder.c` (the ASCII-tag case that serves `TAG_DEPLOYMENT_ID`),
`image_task.c` (where `exif_input` is filled, next to the `Deployment ID` block).

**Context:** The website scores each frame partly on its burst neighbours and today groups by
EXIF timestamp gap (10 s, because current firmware spaces burst frames 3 to 5 s apart), which
merges quick re-triggers and breaks when the RTC rewinds after a cold boot. Burst rules: section
5 of the
[evidence-pipeline architecture report](https://github.com/wildlifeai/ww-website/blob/dev/documentation/development%20reports/2026-09_evidence-pipeline-architecture/README.md)
(lands with ww-website `docs/evidence-pipeline-architecture`).

## Proposed tag

One optional ASCII tag, written like `TAG_DEPLOYMENT_ID` (0xF200): null-terminated, omitted when
the input pointer is NULL. `0xF300` is reserved (`TAG_WW_CONFIDENCE_BASE`), so 0xF201.

```c
/* exif_builder.h */
TAG_CAPTURE_SEQUENCE   = 0xF201,   /* "<trigger>/<index>/<count>/<source>" */

typedef struct {
    ...
    const char *deployment_id;     /* UUID string, or NULL if no deployment active */
    const char *capture_sequence;  /* "<trigger>/<index>/<count>/<source>", or NULL */
    ...
} ExifInput_t;
```

Value, ASCII, at most 24 characters:

```text
<trigger>/<index>/<count>/<source>

trigger  decimal OP_PARAMETER_SEQUENCE_NUMBER of the FIRST frame of this burst
         (read once when the capture sequence starts and held for the burst)
index    1-based frame number within the burst (1..count)
count    OP_PARAMETER_NUM_PICTURES at the time of the trigger
source   one letter: M motion-detect wake, T timelapse wake, C console or app command

examples: "1043/1/3/M"  "1043/2/3/M"  "1043/3/3/M"  "1046/1/1/T"
```

`trigger` reuses the file sequence number because it is already persisted across DPD, monotonic
and unique per card, so no new op-parameter is needed.

## Website side, once the tag ships

`domain/exif.py` adds `0xF201: "Capture_Sequence"` and surfaces `trigger_id`, `frame_index`,
`frame_count`, `trigger_source` into `media.exif_metadata`; the burst grouper then groups on
`(deployment_id, trigger_id)` and ignores timestamps. Until then the 10 s gap stays the
fallback for every image taken before the change.

## Acceptance criteria

- Every JPEG of a motion burst carries 0xF201 with the same `trigger` and `index` 1..`count`;
  a timelapse frame carries `source = T`; the tag is absent when the input is NULL.
- `_Documentation/` lists 0xF201 beside 0xF200 in the EXIF contract table.
- A bench capture of a 3-frame motion burst plus one timelapse frame, checked with
  `exiftool -s -u`, is attached so the website can add a parser test from real bytes.
