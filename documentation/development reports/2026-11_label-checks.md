# Label checks with DINOv3 fingerprints

> **Status:** 📋 Proposal, decisions taken 2026-11-21 (a design grill with Victor). Steps 1 and 3 built 2026-11-27, step 1 behind `FF_LABEL_CHECKS_ENABLED` on dev; step 2 not built. Work is tracked in the issues listed under [Work](#work).

The Wildlife Brain already gives every AI-detected animal a DINOv3 fingerprint (an embedding of its crop) and uses them for groups, find-similar, the map, outliers and the grid's "Needs review first" sort ([wildlife-brain guide](../../frontend/src/content/guides/wildlife-brain.md)). None of those look at the labels: that sort's "disagreement" is the AI and a person disagreeing on the same photo. These checks compare each label with the labels of animals that look like it, to catch likely mistakes for a second look.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Purpose | One tool over every label, AI or human, ranked together |
| 2 | Compared with | The label's project; where a species has too few examples there, also the other projects the user can see |
| 3 | A species' reference | Its human-confirmed labels; AI labels only when there are too few confirmed ones, and such checks rank lower |
| 4 | Odd one out | A label far from every other example of its species (its nearest same-species neighbours), so normal variation such as night against day is not flagged |
| 5 | Look-alike | A label whose nearest look-alikes mostly carry another species: a flag on the photo ("looks like ship rat, labelled Norway rat") and a list of the species pairs most often confused in the project |
| 6 | Mixed group | A Wildlife Brain group whose members carry more than one species is marked, and "confirm the whole group" warns first |
| 7 | Where | A "Needs a second look" filter in Media with the reason on each card, and a small badge on flagged photos in the grid. No new page |
| 8 | Confused pairs | Listed inside that filter; choosing a pair shows both species' flagged photos |
| 9 | Clearing a flag | "Checked, it's right" clears it until the label or its fingerprint changes; a relabel clears it too |
| 10 | Drawn boxes | Saving a box writes a crop file at once (CPU) and the next GPU run fingerprints it. Labels without a box stay unchecked |
| 11 | When | Recomputed per project after each Wildlife Brain run, and nightly for projects whose labels changed that day. A relabelled photo's own flag clears at once |
| 12 | Privacy | Other projects' fingerprints only help score; a reviewer sees photos from their own project only, and a flag based only on other projects says so |
| 13 | Order | Confirmed labels first, then AI labels; within each, strongest flag first, AI-reference checks lower |
| 14 | Sensitivity | A slider in the filter, its default set from feedback (15) |
| 15 | Proving it | Behind a feature flag on dev; the share of flags that end in a relabel against "Checked, it's right" sets the default |
| 16 | Order of work | 1) the checks on today's fingerprints; 2) crops and fingerprints for drawn boxes; 3) the mixed-group warning |
| 17 | Broader and narrower names | Not a conflict when one taxon contains the other (`Rattus` and `Rattus rattus`): a "could be more specific" hint, ranked last. Lineage from the `taxa` table; a label without a `taxon_id` compares by name |

Assumed: only animal labels are checked (never people, vehicles or blanks); anyone who can confirm a label can mark it checked; the fingerprints are the Brain's DINOv3 ViT-H+ (`dinov3-vith`).

## The signals

Distances are cosine distances between fingerprints in the pgvector store. `k`, the minimum examples per species and the thresholds are set when the checks are built and tuned through decision 15.

| Signal | Computed as | Flag |
|---|---|---|
| Odd one out | Mean distance to the `k` nearest fingerprints carrying the same species, within the reference set (decisions 2, 3) | Above the slider's threshold |
| Look-alike | Among the `k` nearest labelled fingerprints in scope, the share carrying a different species that is not a broader or narrower name of this one | Above the slider's threshold; the flag names the majority other species |
| Could be more specific | As look-alike, where the other label is a narrower name of this one | Shown last |
| Mixed group | A group's members carry more than one species that are not broader or narrower names of each other | A mark on the group and a warning on "confirm all" |

## Data

A flag and its outcome need storage, which belongs to ww-backend: a table (`label_checks`, one row per flag) with the observation, the kind of flag, the score, the other species for a look-alike, whether the reference was confirmed or AI, the embedding run and label it was computed from (so a change clears it), who checked it and when, and the outcome (checked or relabelled). Project members read it; anyone who can confirm a label marks it checked. Filed as [wildlifeai/ww-backend#360](https://github.com/wildlifeai/ww-backend/issues/360). Crop files for drawn boxes use the existing `observations.crop_url`. The mixed-group mark is computed from the existing group assignments.

## Built (step 1, 2026-11-27)

Code: the signals in [`domain/label_checks.py`](../../backend/app/domain/label_checks.py), the reads and the row rules in [`domain/label_check_runs.py`](../../backend/app/domain/label_check_runs.py), when it runs in [`jobs/label_checks.py`](../../backend/app/jobs/label_checks.py), the grid's side in [`lib/labelChecks.ts`](../../frontend/src/lib/labelChecks.ts) and [`hooks/useGridSecondLook.ts`](../../frontend/src/hooks/useGridSecondLook.ts). Routes: [API reference](../resources/api-reference.md#label-checks).

| Topic | As built |
|---|---|
| A label | The photo verdict (the label its card shows), animals only; one fingerprint per photo (`media_embeddings`, `dinov3-vith`) |
| Scope | The label's own project only. Borrowing other projects' fingerprints (decision 2) waits for a `label_checks` column saying a flag rests on other projects, which decision 12 needs; none exists |
| Defaults | `k` 5 (`LABEL_CHECK_NEIGHBOURS`), 5 examples for a reference (`LABEL_CHECK_MIN_EXAMPLES`), odd one out at a robust z-score of 3 (`LABEL_CHECK_ODD_Z`); untuned, for decision 15 |
| Look-alike pool | Each species' reference, or all its labels when it has too few for one |
| Score | 0.5 (just flagged) to 1 for every kind: the look-alike share; for odd one out, 0.5 at the z threshold and 1 at twice it |
| Slider | Sensitivity 0 to 100 shows scores from 1 down to 0.5; starts at 80 (0.6) until decision 15's feedback sets it |
| When | A job per project after each Brain run; nightly at `LABEL_CHECK_NIGHTLY_HOUR_UTC` (14) for projects with an observation changed in the last day, from the Lambda tick or the Azure API's reaper loop; `POST .../recompute` on request |
| Rows | Open flags are updated; one no longer flagged becomes `relabelled` if the photo's label changed, else is deleted; `checked` stays until its label or embedding run changes and it is flagged again; the grid drops a flag at once when the photo's label differs |
| Where | Review tab, *Needs a second look*: the flagged photos in decision 13's order, the first 100 at a time, the reason and *Checked, it's right* on each card, the slider and up to 8 confused pairs; a badge on flagged photos in the grid |
| Feedback (15) | `GET /api/label-checks/{project_id}` gives `checked` against `relabelled`; across projects, read-only: `select kind, outcome, count(*) from label_checks group by 1, 2` |

## Work

| Step | Issue |
|---|---|
| 1. The checks, the filter, the slider and "Checked, it's right" | [#448](https://github.com/wildlifeai/ww-website/issues/448), built (above); production waits for ww-backend to promote `label_checks` |
| 2. Crop files and fingerprints for drawn boxes | [#449](https://github.com/wildlifeai/ww-website/issues/449) |
| 3. The mixed-group warning | [#450](https://github.com/wildlifeai/ww-website/issues/450), built: [`domain/cluster_labels.py`](../../backend/app/domain/cluster_labels.py) behind `GET /api/brain/clusters/{deployment_id}/labels`, lineage in [`domain/taxon_lineage.py`](../../backend/app/domain/taxon_lineage.py) |
