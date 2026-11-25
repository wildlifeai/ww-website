# Person detection, end to end

> **Status:** 🔧 Active, not scheduled. 2026-11-26: [#140](https://github.com/wildlifeai/ww-website/issues/140)
> and [#135](https://github.com/wildlifeai/ww-website/issues/135) are closed and edge reflection
> types a person class as `human`; a step 4 rerun that ends in a Camera AI row is still to do, with
> no issue. Steps 1 to 3 done on dev and real hardware, 2026-09-05. Step 4 ran
> the same day with a second capture set and got as far as media rows and Cloud AI labels,
> but **no Camera AI row**: the deployment's project points at the *seeded* Person Detection
> (`1V1`), whose `label_map` is `{}`, so `reflect_edge_deployment` skips the deployment
> entirely. See [§5](#5-step-4-first-run-2026-09-05) for that run and what it exposed. See
> [2026-09-05_bench-run-from-the-mobile-app.md](2026-09-05_bench-run-from-the-mobile-app.md)
> for the device side and [#140](https://github.com/wildlifeai/ww-website/issues/140) for
> what the upload does with the EXIF deployment id.

The first model to travel the whole embedded path on purpose, with every stage checked:
upload through the pretrained path → `label_map` → transfer to the device from the mobile
app (website SD-card prep as the backup) → capture → upload → a Camera AI observation
beside the Cloud AI one on the website. Rat detection follows the same runbook once
[ww-backend #175](https://github.com/wildlifeai/ww-backend/issues/175) gives it its own
labels file.

Came out of [#134](https://github.com/wildlifeai/ww-website/issues/134) (a two-class
person detector shipped with a one-line labels file). Fixing that in
[#136](https://github.com/wildlifeai/ww-website/pull/136) and
[#139](https://github.com/wildlifeai/ww-website/pull/139) turned up why the labels were
wrong, a regression in #136 itself, and a firmware defect that decides which models the
device can run at all. Those are recorded in §1 to §3 because they shape the runbook; the
runbook is §4.

## 1. Why every pretrained model shipped `unknown`

`MODEL_REGISTRY` declares `labels` once per architecture, but `get_model_config` copied only
the per-resolution entry, so `config.get("labels", ["unknown"])` in
`convert_github_pretrained_model` missed every time. Not only Person Detection: YOLOv8/v11
OD and YOLOv8 Pose all packaged as `["unknown"]`. `manifest.py` read the same value from the
right level and `/pretrained/catalog` advertised the real names, so the UI showed
"no person, person" while storage got `unknown`.
[`logs/bucket_audit_before_2026-09-05.log`](logs/bucket_audit_before_2026-09-05.log) shows
the three `unknown` files in the dev bucket. Fixed in #139.

## 2. What the device can run

Read on firmware `dev` at `3ca2823c`, `ww500_md`:

- `cvapp.cpp:609` sets `g_class_count` from the output tensor's **last** dimension, into a
  `uint8_t`; `cvapp.cpp:835` reads the **second** dimension for the softmax. They agree only
  for a rank-2 `[1, C]` classifier.
- `cvapp.cpp:818` copies `g_class_count` bytes into `int8_t outCategories[MAX_CLASSES]`
  (`image_task.c:768`, `MAX_CLASSES = 16`) with no clamp.
- `processNNOutput` (`image_task.c:2458`) reports **class index 1** as the target, above its
  own `// TODO This only works for the person detection`.
- The class/label mismatch check at `cvapp.cpp:618` only warns.

So the firmware is a classifier pipeline, `[1, C]` with `C <= 16`, `[background, target]`
in that order, and a detection head overflows the stack:

| model | output shape | last dim | `g_class_count` (u8) | writes into `int8_t[16]` |
|---|---|---|---|---|
| person detect | `[1, 2]` | 2 | 2 | safe |
| rat detect (zoo, 48×48) | `[1, 2]` | 2 | 2 | safe |
| YOLOv8 Pose | `[1, 256, 64]` | 64 | 64 | +48 bytes |
| YOLOv11 OD 224 | `[1, 28, 28, 144]` | 144 | 144 | +128 bytes |
| YOLOv8 OD 192 | `[1, 4, 756]` | 756 | 244 | +228 bytes |
| YOLOv11 OD 192 | `[1, 84, 756]` | 756 | 244 | +228 bytes |

Shapes: [`logs/firmware_zoo_output_shapes_2026-09-05.log`](logs/firmware_zoo_output_shapes_2026-09-05.log).
Filed as [firmware #225](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/225).
Not observed on hardware; the overflow is reachable by reading the path, and whether a
2 MB model gets that far before failing on arena or flash limits is unverified.

**Decision (2026-09-05):** the website registry marks the three detection entries
`blocked`, so they cannot be selected in the catalogue or packaged by either the upload or
the manifest path. They stay in the registry so the URLs, shapes and the reason live in one
place. Detection models are future work, gated on person and rat detection running end to
end first. The contract is written up in
[embedded-model-lifecycle.md § What the device can run](../../resources/embedded-model-lifecycle.md#what-the-device-can-run).

This also means #136's LM-1 check, which read the last output dimension as the class count,
would have refused valid Edge Impulse detection uploads (6 "classes" for a one-class apple
detector). #139 restricts it to rank-2 heads; anything else is "no cross-check available".
That is the right website behaviour for a firmware that cannot run detectors anyway, and it
should be revisited together with #225 when one can.

## 3. Two things the real upload path does that matter here

- **It is idempotent on (family, version) and never repairs.** `upload_and_register` returns
  the existing `ai_models` row for a precompiled model whose family and `version_number`
  already exist, without re-uploading. A re-import cannot fix a bad labels file on an
  existing row; the row (or the family) has to go first.
- **Family resolution is by `firmware_model_id`, then by name.** The registry's Person
  Detection is `fw=20` and is named `Person Detection (96x96)`; the ww-backend seed's is
  `fw=1`, named `Person Detection`. They do not collide, so dev now has both families. The
  device file for the registry one is `20V1.TFL`.

## 4. Runbook

All scripts run from the repo root with the root `.env` (dev credentials). `step1` writes to
dev; the others are read-only.

### Step 1 — upload with correct labels (done, PASS)

```
python "documentation/development reports/2026-09_person-detection-e2e/scripts/step1_upload_person_detection.py"
```

Runs what `POST /api/models/pretrained` enqueues (`download_github_pretrained_job`:
`convert_github_pretrained_model` + `upload_and_register`), then sets `label_map`, then
reads storage and the row back. Evidence:
[`logs/step1_upload_2026-09-05.log`](logs/step1_upload_2026-09-05.log).

| | |
|---|---|
| `ai_models.id` | `a3c7c373-30a2-4692-9ba2-2e69064144eb` |
| family / firmware id | `Person Detection (96x96)` / **20** → device file **`20V1.TFL`** |
| `20V1.TXT` in storage | `b'no person\nperson'`, 16 bytes, LF |
| `detection_capabilities` | `['no person', 'person']` |
| `label_map` | `person` → target, `Homo sapiens`, threshold 50; `no person` → background |
| status | `validated` (the mobile app syncs `validated` and `deployed`) |

`label_map.person.taxon_id` is `None`: `Homo sapiens` is not in `taxa` after the reseed, and
what a person class should assert (`observationType: human` rather than a taxon) is
[#135](https://github.com/wildlifeai/ww-website/issues/135). `edge_reflection` accepts a
`None` taxon and writes `scientific_name`, the same as the seeded Rat Detection, so this does
not block the run. It does mean the edge observation will carry `observation_type: animal`
for a human until #135's `edge_reflection.py:97` fix lands. (2026-11-26: #135 is closed; a class
saved as a type gives a `human` row, `backend/app/domain/edge_reflection.py`.)

### Step 2 — point a project at the model, transfer to the device

1. **Project.** Set `projects.model_id = a3c7c373-…` on the project your test device's
   deployment belongs to. `edge_reflection` resolves the model through
   `deployment → project.model_id`, so a deployment under a project with no model reflects
   nothing. Do not reuse *Sinbad Skink Survey*; it is the Rat Detection demo.
2. **Mobile app (primary).** Sync so the model is on the phone (`validated` is synced). The
   transfer runs inside the deployment sync, `syncAiModel` in
   `src/ble/workflows/deploymentPipeline.ts`; there is no standalone transfer screen any
   more. It downloads **both** `20V1.TFL` and `20V1.TXT`, sends both to `/MANIFEST/`, lists
   the directory to confirm `20V1.TFL` is there, issues `loadmodel 20 1`, then reads back
   OP 14 = 20 and OP 15 = 1
   ([mobile AI-Model-Integration](../../../../ww-mobile-app/documentation/resources/AI-Model-Integration.md)).
3. **Website SD-card prep (backup).** `POST /api/manifest/generate` with
   `{"model_source": "organisation", "org_model_id": "a3c7c373-…"}`, poll the job, unzip
   `MANIFEST.zip` onto the card. Same `20V1.TFL` + `20V1.TXT`.
4. **The retransfer trap.** If the card already holds a `20V1.TXT` from before #139 (the
   7-byte `unknown` one), the sync only sends files the card is missing and will keep the old
   labels. Delete `20V1.TXT` from `/MANIFEST/` or reformat the card first. Check on the
   device console: `There are 2 classes (2)` at model load, and no
   `WARNING: Number of classes and labels unmatched`.

### Step 3 — capture and upload

Put a person in front of the camera and let it capture a few frames (the Engineer Console's
capture test works). Also capture a few empty frames. Upload the card, or the images, to the
deployment on the website as usual. The pipeline runs SpeciesNet / Wildlife Brain on the
same images and then `reflect_edge_deployment`, all behind flags that are on for dev
(`FF_PIPELINE_ENABLED`, `FF_SPECIESNET_ENABLED`, `FF_WILDLIFE_BRAIN_ENABLED`,
`FF_EDGE_REFLECT_ENABLED`).

### Step 4 — verify on the website

```
python "documentation/development reports/2026-09_person-detection-e2e/scripts/step3_verify_deployment.py" <deployment_id>
```

Per media row it prints the device's `UserComment` fields (stage 7) and every observation
split by `ai_origin`. **Pass** is at least one media row with `person` in
`user_comment_fields` *and* an `ai_origin='edge'` observation whose `source_model_version`
matches the project's model, which the script derives and prints (`20V1` for the registry
model, `1V1` for the seeded one; the bench run used `1V1`, see the mobile-app note). In the UI
that is *📟 Camera AI: Homo sapiens NN%* beside *☁ Cloud AI: …* on the annotation panel.

What each failure looks like, and where to look:

| symptom | means | look at |
|---|---|---|
| `UserComment` absent or logits only | firmware side; `USE_PERCENTAGE` or the contract in [#189](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/189) | device console, EXIF of a raw frame |
| `UserComment` has `'': NN%` | old one-line labels file still on the card | step 2.4 |
| `UserComment` fine, no edge observation | `projects.model_id` unset, or `label_map` empty, or flag off | step 2.1; `edge_reflect_skipped` in backend logs |
| edge row present, `observation_type: animal` | the person class is saved as a taxon (before #135 every edge row was `animal`) | re-save the class as a type in the label mapper |

## 5. Step 4, first run (2026-09-05)

A second capture set (`C:\Users\ww\person-test-images-run2\MEDIA\`, 18 frames) was uploaded
through the new `/upload-data` page ([#142](https://github.com/wildlifeai/ww-website/pull/142))
against cloud dev. The set claims three deployments: `01e03d10…` (10 frames, on the server,
project *Personal test*), `ceb77c85…` (7 frames) and `16bed409…` (1 frame), neither of the
last two on the server. The two unknown ones were skipped at triage, so only `01e03d10…`
was uploaded.

| | |
|---|---|
| media rows created | 7 of 10 |
| Cloud AI observations | 7, `speciesnet-v4.0.1a`, `observation_type: human` |
| **Camera AI observations** | **0** |
| camera scores present in EXIF | yes, all 7 frames, e.g. `person: 38%; no person: 62%` |

**Why no Camera AI row.** `projects.model_id` for *Personal test* is the **seeded** Person
Detection (`43a87b0d…`, family firmware id 1, so `1V1`), and its `label_map` is `{}`.
`reflect_edge_deployment` returns early on an empty `label_map`
(`edge_reflect_skipped, reason="no project model or empty label_map"`), so nothing is
reflected regardless of the scores. This is step 2.1 of the runbook not having been done for
this project: point it at the registry model `a3c7c373…` (`20V1`), which does carry a
`label_map`, or give the seeded row one.

**Second reason, independent of the first.** Six of the seven frames score `person` below the
50 % reflection threshold (33 % to 44 %); only `20260905194608_01.jpg` at 54 % would produce a
row even with a correct `label_map`. The camera genuinely judged those frames "no person"
while SpeciesNet called all seven `human`. That disagreement is the interesting result of the
run, and it was invisible in the UI because a below-threshold score creates no observation:
hence the raw per-class *📟 Camera AI on the device* line added to the annotation panel in
#142, which shows the camera's verdict whether or not it crosses the threshold.

**Three frames lost.** One to a 60 s read timeout against `googleapis.com` with no retry, two
to an HTTP/2 `ConnectionTerminated` on the shared Supabase client that killed the second
batch's Drive job while the request still returned 200. Both now retry, and a batch with no
Drive job is counted as failed in the dock (#142). Evidence:
[`logs/step4_api_2026-09-05.log`](logs/step4_api_2026-09-05.log).

## Open items

- **Step 4 needs a rerun** once the deployment's project points at a model with a real
  `label_map`, and with the #142 backend live (the API container bind-mounts the main
  checkout, so the retries and the reflect-at-upload-time change need that branch merged and
  the container recreated). Expect one Camera AI row from the 54 % frame, and the raw-score
  line on all of them.
- **The seeded Person Detection has an empty `label_map`** (`43a87b0d…`, `1V1`). The same
  class of seed gap as [ww-backend #175](https://github.com/wildlifeai/ww-backend/issues/175);
  worth raising there so a freshly seeded dev can reflect edge scores without a manual fix.
- **Steps 2 and 3 are done on hardware** (5 September 2026): the device confirmed
  `There are 2 classes (2)` at model load. Read
  [2026-09-05_bench-run-from-the-mobile-app.md](2026-09-05_bench-run-from-the-mobile-app.md)
  first: the model that ran is `1V1`, not the `20V1` this runbook names, so the pass
  condition's `source_model_version` differs, and `FF_EDGE_REFLECT_ENABLED` was never
  reaching the container until that session fixed `docker-compose.yml`.
- [#140](https://github.com/wildlifeai/ww-website/issues/140): the upload resolved deployments
  from the card folder only, while every frame (the `MEDIA/00000000/` one included) carries
  the full id in EXIF `0xF200`. Fixed alongside this report: EXIF first, folder second, and
  the triage screen creates a missing deployment under the stamped id so the phone's later
  sync converges on it. The phone-side non-push (no `users` row or project role for the
  account in dev, so the `deployments` INSERT policy rejects it and `push_changes` reports
  `not_applied`) is a mobile-app issue.
- [#135](https://github.com/wildlifeai/ww-website/issues/135): what a `person` class asserts;
  `edge_reflection.py:97` hardcodes `animal`.
- [ww-backend #175](https://github.com/wildlifeai/ww-backend/issues/175): Rat Detection's own
  labels file, then repeat this runbook for rat.
- [firmware #225](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/issues/225):
  refuse to load on class/label mismatch, clamp the result copy.
- Trailing newline: ww-backend's `upload_person_model.py` writes `no person\nperson\n`
  (17 bytes); the website path writes no trailing newline (16 bytes). Both load on the
  device today as far as we know; unverified which the parser prefers.
- `uploaded_by` on the step-1 row is NULL because `auth.admin.list_users()` failed against dev
  (`Database error finding users`). Unrelated to the run, not chased.

## Outcome

Not yet reached. The website half is verified: a correctly labelled Person Detection model
can be produced by the real path and lands in storage and the table in a consistent state.
The device half is the next session.
