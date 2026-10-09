# Wildlife Watcher — Dual-Layer AI Architecture

**Status:** Adopted · 2026-07-08 · v0 built and merged (2026-07-10)

> **Build progress.** Shipped and merged: the **`AI-ARCHITECTURE.md`** umbrella (now the canonical
> naming doc — prefer it over §1 here), the **user guide**, lifecycle **stage 8**
> (`domain/edge_reflection.py`, gated on `FF_EDGE_REFLECT_ENABLED`, **on for dev**), the **LM-1/2**
> label-chain checks in `domain/model.py`, and the ww-backend **`dual_ai_v0`** schema
> (`observations.ai_origin` + `device_alert_rules`) — **merged**, so edge reflection is unblocked.
> Still open for v0: the firmware `USE_PERCENTAGE` fix + `LM-9` guard (cross-repo — **the gating item**),
> LM-5 + the golden-set parity harness, and seed/test data (a project model with a real `label_map`
> + media carrying UserComment NN scores). See §4.1 and
> [dual-ai-production-rollout](../resources/dual-ai-production-rollout.md).
>
> ⚠️ **Where this doc has been overtaken:** the alert-rules table shipped as **`device_alert_rules`**,
> not `alert_rules` as sketched in §3.2, and the build breakdown §2.2 calls for now exists as
> [lorawan-alert-execution-spec](./lorawan-alert-execution-spec.md). The design reasoning in §3
> (modes, ownership split, fair-use) is still current.
**Scope:** Naming, integration architecture, LoRaWAN alert logic, and validation roadmap for the two AI approaches (Cloud AI and Edge AI), grounded in the current state of `ww-backend`, `ww-website`, `Seeed_Grove_Vision_AI_Module_V2` (Himax firmware), `ww-hardware` (Nordic firmware), and `wwmobile`.

**Companion canon (already exists — this document does not replace it):**

| Doc | Owns |
|---|---|
| `ww-website/documentation/onboarding/04-AI-PIPELINE.md` | Cloud pipeline (SpeciesNet, BioCLIP, Wildlife Brain) |
| `ww-website/documentation/resources/embedded-model-lifecycle.md` | Edge model end-to-end map (stages 1–8) |
| `ww-website/documentation/resources/ai-model-pipeline.md` | Upload/convert/manifest detail |
| `ww-backend/documentation/resources/DATABASE_REFERENCE.md` | Schema (`ai_model_families`, `ai_models`, `observations`, `annotation_runs`) |
| `Seeed_Grove_Vision_AI_Module_V2/_Documentation/Operational_Parameters.md` | OP parameters (OP 14/15/16 …) |

---

# 1. System Nomenclature & Taxonomy Proposal

## 1.1 The three layers (not two)

The codebase already contains a **third** AI layer — the DINOv3 embedding/clustering track ("Wildlife Brain"). Naming must account for it or users will conflate it with the cloud classifier.

| # | Canonical technical name | User-facing name | What it is | Where it runs |
|---|---|---|---|---|
| 1 | **Edge AI** | **Camera AI** | Custom int8 TFLite classifier (built in Edge Impulse, Vela-compiled for the Ethos-U55 NPU) running on the WW500's Himax HX6538 | On the camera |
| 2 | **Cloud AI** | **Cloud AI** (descriptor: *"the gold-standard check"*) | SpeciesNet ensemble (detect + classify) + optional BioCLIP second opinion, auto-run on every upload | Website backend / GPU worker |
| 3 | **Wildlife Brain** | **Wildlife Brain** | DINOv3 embeddings → HDBSCAN clustering → active learning, QA, and conservation intelligence | Website backend / GPU worker |

Rules of use:

- **"Camera AI" vs "Cloud AI"** is the pair every UI string, guide, and notification uses. They are parallel, two-word, self-explanatory, and already consistent with the sketched annotation-panel copy (*📟 Device: rat 87%* / *☁ SpeciesNet: Rattus rattus 64%*) — which should become *📟 Camera AI* / *☁ Cloud AI*.
- **"Edge AI" / "Cloud AI"** are the code- and docs-level terms (module names, enum values, doc titles). "On-device", "embedded model" remain acceptable synonyms in firmware-internal docs, but new cross-repo writing should say Edge AI.
- **"Wildlife Brain"** is reserved for the embedding/discovery layer only. Never use "brain" loosely for the other two — which leads to:

## 1.2 The model artifact: "Species Brain"

The UI already calls the deployable model "the specific AI 'brain'" and "Species Brain" (`GenerateManifest.tsx`). Keep it, but pin the definition:

> **Species Brain** *(user-facing)* = one trained, versioned Edge AI model assigned to a project — e.g. "Rat Detection v10". Technical equivalent: an `ai_models` row (an **edge model version**) inside an `ai_model_families` family.

This gives non-technical users a countable noun ("your project's Species Brain", "update the Species Brain") without colliding with Wildlife Brain, because one is *on the camera*, the other is *the website's intelligence layer* — and guides should say exactly that once, in a glossary.

## 1.3 Full vocabulary table

| Concept | Technical (code/DB/docs) | User-facing (UI/guides) | Do NOT call it |
|---|---|---|---|
| On-device inference layer | Edge AI | Camera AI | "Edge Impulse AI" (vendor ≠ layer), "TFLite model" |
| Cloud inference layer | Cloud AI (pipeline) | Cloud AI | "MegaDetector" (removed — SpeciesNet subsumed it) |
| Embedding/discovery layer | Wildlife Brain | Wildlife Brain | "clustering AI" |
| Deployable edge model version | edge model / `ai_models` row | Species Brain | "the brain" (ambiguous) |
| Model concept across versions | model family (`ai_model_families`) | — (invisible to users) | — |
| Device-facing model identity | `firmware_model_id` (OP 14) + `version_number` (OP 15) → `{id}V{ver}.TFL` | — | — |
| SD deployment package | manifest / `MANIFEST.zip` | SD Card Setup Folder | "firmware" (it contains firmware *and* more) |
| A single AI result | observation (`source_type='ai'`) | detection / identification | "annotation" (retired subsystem) |
| Edge result carried in the image | EXIF UserComment scores (`"rat: 87%; …"`) | Camera AI result | — |
| Alert strategy 1 | `instant` | Instant Alert | — |
| Alert strategy 2 | `digest` | Daily Digest | — |
| Alert strategy 3 | `backoff` | Smart Back-off | "decay" in UI (fine in specs as synonym) |

## 1.4 Provenance vocabulary in the data model

When stage 8 (reflecting Camera AI predictions as observations) is built, both layers write to `observations` with `source_type='ai'`. Distinguish them with an explicit, queryable origin rather than string conventions:

```sql
-- ww-backend/supabase/schemas/public/tables/35_observations.sql  (non-breaking addition)
ALTER TABLE observations ADD COLUMN ai_origin text
  CHECK (ai_origin IN ('edge', 'cloud'));  -- NULL for human/imported rows
```

- `ai_origin='cloud'` + `source_model_version='speciesnet-…'` / `'bioclip-2'` (existing convention).
- `ai_origin='edge'` + `source_model_version='{firmware_model_id}V{version_number}'` — the same identity that names the `.TFL` file, closing the provenance loop from device filename → EXIF → observation row.

---

# 2. Architectural Integration Framework

## 2.1 Current state (verified against code, 2026-07-08)

```
                         ┌──────────────────  EDGE (Camera AI)  ─────────────────────┐
 Edge Impulse (user) ─► website POST /api/models/convert ─► Vela compile (Ethos-U55) │
   int8 .tflite zip        job: uploading→uploaded→validated/failed                  │
                           artifacts: {fwId}V{ver}.TFL + labels .TXT (ai-models bucket)
                                        │
              ModelLabelMapper → ai_models.label_map  (label → target|background, taxon, threshold)
                                        │
             projects.model_id ─► "Prepare SD Card" job ─► MANIFEST folder            │
             (per-project brain)   (model + labels.txt + CONFIG + Himax firmware,     │
                                    both camera variants RP3 / HM0360)               │
                                        │                                            │
        mobile app (BLE loadmodel, OP14/15)  or  SD card MANIFEST ──► WW500 device   │
                                        │                                            │
              Himax HX6538: motion detect → capture → NN inference (OP16 threshold)  │
              → EXIF: DeploymentID 0xF200 · MakerNote 0x927C (AE) ·                  │
                UserComment 0x9286 "label: pct%; …"  [⚠ blocked: USE_PERCENTAGE]     │
              → I2C events to Nordic nRF52832: OP1 totalNNEvents, OP2 positiveNNEvents
              → Nordic LoRaWAN uplink (legacy TrapNZ payload: counters only)         │
                         └───────────────────────────────────────────────────────────┘
                                        │  (SD card collected / images uploaded)
                         ┌──────────────────  CLOUD (Cloud AI)  ───────────────────┐
 upload → exif.py ingest (deployment id, user_comment_fields)                      │
        → auto pipeline: MEDIA_PREP → SPECIESNET → ANIMAL_CROP → BIOCLIP           │
          (GPU worker, KEDA-scaled on api_jobs; per-run annotation_runs row)       │
        → observations (bbox, confidence, taxon rollup, blank-as-positive)         │
        → Wildlife Brain: DINOv3 embed → cluster → review queue → QA agreement     │
                         └─────────────────────────────────────────────────────────┘
```

Two truths worth stating in every architecture conversation:

1. **The layers are complementary, never exclusive.** The same image can (and should) carry both a Camera AI result (EXIF) and a Cloud AI result (observations). Cloud AI is the accuracy reference; Camera AI is the low-power, real-time trigger.
2. **The whole edge→cloud reflection loop currently hinges on one firmware flag.** `USE_PERCENTAGE` is compiled out (`cvapp.h:33`), so UserComment is never written and stages 7–8 of the lifecycle have nothing to ingest. This is the highest-leverage single fix in the system (see roadmap V0).

## 2.2 Documentation structure

Keep detail in the repo that owns the stage (matching existing repo doc rules); keep exactly **one** cross-repo map per concern; add the missing umbrella:

| Level | Doc | Status |
|---|---|---|
| Umbrella (both layers + alerting) | `ww-website/documentation/resources/AI-ARCHITECTURE.md` — essentially §1–§3 of this proposal, linked from all four repos' READMEs | ✅ created (this branch) |
| Edge map | `embedded-model-lifecycle.md` (stages 1–8) | exists, current |
| Cloud map | `04-AI-PIPELINE.md` | exists, current |
| Stage detail | per-repo (`ai-model-pipeline.md`, firmware `_Documentation/`, mobile `AI-Model-Integration.md`, backend `DATABASE_REFERENCE.md`) | exists |
| Alerting spec | [`lorawan-alert-execution-spec.md`](./lorawan-alert-execution-spec.md) — the build breakdown across all four repos (Nordic owns device behaviour; backend owns rules schema + decoder) | ✅ created |
| User-facing | **NEW guide** via the website `guide-author` skill, category `Analysis`: *"How Wildlife Watcher's two AIs work together"* — Camera AI / Cloud AI / Species Brain glossary, one diagram, no vendor names. Respect the canon rule: LoRaWAN = "in development" until §3 ships | ✅ created (this branch) |

Conventions for all AI docs: state **which layer** in the first sentence; name the owning repo per stage; every claim of device behaviour links to firmware code or `_Documentation/`; known gaps get a ⚠ block (the lifecycle doc's pattern — keep it).

## 2.3 How the layers combine dynamically

Four integration patterns, in order of maturity:

**P1 — Filter → Verify (now, once `USE_PERCENTAGE` ships).** Camera AI decides *what is worth alerting on* in real time; Cloud AI re-judges every image on upload regardless. Website shows both: *📟 Camera AI: rat 87%* beside *☁ Cloud AI: Rattus rattus 64%*, mapped to taxa via `ai_models.label_map` (background classes skipped). Canonical label precedence: **human > cloud > edge**.

**P2 — Agreement telemetry (near-term).** Because both layers label the same media, compute per-model-version precision of Camera AI against Cloud AI + human review (extend the existing `qa_report()` AI-vs-human agreement). Output: "Rat Detection v10 agrees with gold standard 91% at ≥80% confidence" on the model page. This is the *trust dial* for alert thresholds: PMs set alert confidence knowing the measured false-alert rate.

**P3 — Retraining loop (the flywheel).** Human-reviewed observations (+ Wildlife Brain cluster-confirmed labels and animal crops) export as an Edge Impulse-ready dataset → user retrains → uploads → new `version_number` in the same family → devices update via manifest/BLE. Cloud AI is the labelling engine for its own edge competitor.

**P4 — Alert verification round-trip (with §3).** A LoRaWAN alert is a *provisional* claim ("Camera AI: rat ≥80%"). When the SD card is later uploaded, the platform matches alert timestamps to media and marks each alert **confirmed / refuted** by Cloud AI + human review — retrospective alert precision per rule, feeding back into P2.

## 2.4 Custom model pipeline for advanced users (canonical workflow)

Stages 1–5 are **built**; the deltas are marked.

1. **Train & export** (user, Edge Impulse): full **int8-quantized** TFLite export. Float models fail Vela with "FullyConnected could not run on NPU" — the converter already surfaces the real error.
2. **Upload & convert** (website): `POST /api/models/convert` → `convert_model_job` → Vela → `.TFL` + labels `.TXT` in the `ai-models` bucket; `ai_models` row `uploading → uploaded → validated | failed`, org-scoped via RLS.
   *Delta:* add an automated **bench-inference check** to validation — run the compiled model on a 10-image golden set server-side (TFLite reference runtime) and store scores in `processing_log` for later device parity checks (§4 LM-checks).
3. **Label mapping** (website, `ModelLabelMapper`): uploader declares each output class target-vs-background + taxon → `ai_models.label_map`. *Make this a required gate before a model can be assigned to a project* — today it is the only thing standing between "rat: 87%" and a real *Rattus rattus* record.
4. **Assign** (website): `projects.model_id` → the project's Species Brain.
5. **Deploy**: Prepare SD Card manifest (fast path) or mobile BLE `loadmodel` (OP 14 + OP 15). Only `validated`/`deployed` models sync.
   *Delta:* add `deployed_at` telemetry — when a device first reports OP14/15 matching a version (via BLE stats or heartbeat), stamp the model row; PMs currently cannot see fleet model adoption.
6. **Observe & iterate**: P2 agreement telemetry per version → retrain (P3).

*Schema note:* all edge-model additions above are non-breaking (new nullable columns / new tables) and follow the backend workflow — edit `supabase/schemas/`, `DB_AGENT_MODE=1 npm run db:change …`, never touch generated artifacts.

---

# 3. LoRaWAN Alert Logic Specification

## 3.1 Current state and constraints

- Today's uplink (`ww-hardware/…/app_ww.c`, `generate_wildlifeWatcherPayload`) is **TrapNZ-legacy**: heartbeat (12 B: event, status, `totalNNEvents`, sw version, ping period, battery) or sprung/set (8 B: + `positiveNNEvents`). Counters only — no class, no confidence, no model identity. It must keep working (TrapNZ integration).
- The **Nordic nRF52832 owns alert logic**: it owns the LoRa stack, timers, and config (`CONFIG.TXT` format is Nordic-owned); the Himax owns inference and reports events/stats over I2C. Today the Himax only forwards OP counters; it must additionally forward **(class_index, score)** per positive inference — a small I2C message extension (coordinate across both firmware repos; the message-type enums must stay in lockstep).
- Threshold semantics: OP 16 is a **logit threshold 0–127**, not a percentage. Rule compilation on the website must convert UI percent → device units using the deployed model's output-tensor quantization (scale/zero-point), never a hardcoded mapping (validated in §4 LM-5).
- LoRaWAN fair-use: alerts must be rate-limited and duty-cycle aware; **instant alerts use confirmed uplinks** (retry ≤ 3), digest/heartbeat unconfirmed.

## 3.2 Rule configuration (backend → device)

**New table (backend-owned, non-breaking).** ⚠️ This shipped as **`device_alert_rules`** in the
ww-backend `dual_ai_v0` migration — the sketch below kept its working name:

```sql
CREATE TABLE device_alert_rules (   -- as shipped; sketched here as `alert_rules`
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id       uuid NOT NULL REFERENCES projects (id),
  model_family_id  uuid NOT NULL REFERENCES ai_model_families (id),
  label            text NOT NULL,          -- must exist in the deployed version's label_map, role='target'
  mode             text NOT NULL CHECK (mode IN ('instant', 'digest', 'backoff')),
  threshold_pct    smallint NOT NULL CHECK (threshold_pct BETWEEN 1 AND 100),
  digest_send_utc  smallint CHECK (digest_send_utc BETWEEN 0 AND 23),      -- digest only
  backoff_steps_min integer [] DEFAULT '{5,30,120,720}',                   -- backoff only
  clear_window_min  integer DEFAULT 60,                                    -- backoff only
  enabled          bool NOT NULL DEFAULT true,
  created_by       uuid REFERENCES auth.users (id),
  created_at       timestamptz DEFAULT now(),
  updated_at       timestamptz DEFAULT now()
);
```

Rules are edited by **project admins** (RLS mirrors `projects`). The manifest job **compiles** enabled rules into the device config (one line per rule, class *index* resolved from labels order at compile time):

```
# MANIFEST CONFIG.TXT additions (Nordic-owned format — coordinate before shipping)
ALERT=<class_idx>,<mode:0|1|2>,<threshold_logit>,<p0>,<p1>,<p2>,<p3>,<clear_min>
DIGEST_HOUR=<0-23 UTC>
```

Same rules deployable over BLE for in-field updates (mobile app, via the existing command queue — one command at a time). LoRaWAN **downlink** rule updates are a later phase; config via SD/BLE first.

## 3.3 Uplink payload v2

Keep the legacy payload untouched on its current port. Add **port 10, payload_version 0x02**. Every alert-bearing message carries the model identity so the server can decode class indices → labels → taxa via `ai_models.label_map` — *the device never transmits label strings*:

```
Common header (5 B): [0]=0x02 version · [1]=msg_type · [2..3]=firmware_model_id LE · [4]=model_version_number

msg_type 0x01 INSTANT/BACKOFF ALERT (11 B total):
  [5]=class_index · [6]=confidence_pct · [7]=backoff_stage (0=first)
  [8..9]=detections_since_last_alert LE · [10]=battery_level

msg_type 0x02 DIGEST (6 + 4·n B):
  [5]=n_classes · then per class: [class_index, max_confidence_pct, count LE×2]

msg_type 0x03 ALL-CLEAR (7 B): [5]=class_index · [6]=battery_level
  (sent once when a backoff class returns to IDLE — lets dashboards show "target gone")
```

Server side: uplink webhook (TTN/ChirpStack) → decoder job → new `device_alert_events` table → notifications fan-out (aligns with the in-progress notifications work). Decoder joins `(firmware_model_id, version_number)` → `ai_models` → labels order + `label_map` → taxon + display name.

## 3.4 Alert strategies — state machines

All logic lives on the Nordic, per configured class. Global guard on top of every strategy: `MIN_UPLINK_GAP` (default 120 s) between alert uplinks; if multiple classes fire inside the gap, coalesce into one uplink queue, oldest first.

**Common event input** (from Himax over I2C after each inference):
`on_detection(class_idx, score)` — already thresholded per OP 16 *plus* per-rule threshold check on the Nordic (rule thresholds may be stricter than the global OP 16).

### Instant

```
state: last_alert_at[class]

on_detection(c, score):
  if score < rule[c].threshold: return
  if now - last_alert_at[c] < MIN_UPLINK_GAP: pending[c].count++; return
  send_alert(c, score, stage=0, count=pending[c].count+1, confirmed=true)
  pending[c].reset(); last_alert_at[c] = now
```

### Daily Digest

```
state: digest[class] = {count, max_score}   // retained across sleep (see 3.5)

on_detection(c, score):
  if score >= rule[c].threshold: digest[c].count++; digest[c].max_score = max(...)

on_rtc(DIGEST_HOUR utc, once per day):
  if any digest[c].count > 0:
      send_digest(all classes, unconfirmed)
  reset all digest[c]         // on send failure: retry at next heartbeat, then reset
```

### Smart Back-off (decay)

```
config: steps = [5, 30, 120, 720] minutes · clear_window = 60 min
state per class: phase ∈ {IDLE, ACTIVE} · stage · suppress_until · last_seen · suppressed_count

IDLE:
  on_detection(c, score ≥ threshold):
      send_alert(c, score, stage=0, count=1, confirmed=true)
      phase=ACTIVE; stage=0; suppress_until=now+steps[0]; last_seen=now

ACTIVE:
  on_detection(c, score ≥ threshold):
      last_seen = now
      if now < suppress_until: suppressed_count++          // stay quiet
      else:
          stage = min(stage+1, len(steps)-1)
          send_alert(c, score, stage, count=suppressed_count+1, confirmed=true)
          suppressed_count = 0; suppress_until = now + steps[stage]

  every CLEAR_CHECK (e.g. 15 min, piggybacked on existing wake cycles):
      if now - last_seen > clear_window:
          send_all_clear(c, unconfirmed)                    // optional but recommended
          phase=IDLE; stage=0; suppressed_count=0           // next sighting alerts instantly
```

Behaviour: first rat → alert now; rat stays → next alerts at +5 min, +30 min, +2 h, then every 12 h, each alert carrying how many detections were suppressed; rat gone for `clear_window` → reset, so the *next* rat alerts instantly again. This matches the requested 5 min → 30 min → 2 h → 12 h → reset contract exactly, with the step array configurable per rule.

## 3.5 Embedded realities the spec must respect

- **Persistence:** alert state must survive deep sleep (retained RAM) — it's what makes back-off meaningful across wake cycles. On **reboot/power-loss**, state resets to IDLE by design (a fresh boot alerting instantly is acceptable; spurious "first" alerts after battery swaps are not a failure). Digest counters may optionally persist to flash (FDS) at digest boundaries only — no per-detection flash writes.
- **Clocks:** digest send time needs real time — the LoRa stack's `DeviceTimeReq` (already issued on heartbeats) covers it; if time is unsynced, digest falls back to "every 24 h since boot".
- **Class count is small** (EXIF pipeline caps at 4 dynamic classes; models are 2–5 classes) — per-class state is a few bytes; no memory concern, but cap configurable rules at 8/device.
- **Never block inference on radio**: alert dispatch is queued; the Himax↔Nordic I2C exchange stays fire-and-forget.

---

# 4. Validation Roadmap & User Stories

## 4.0 Label-mapping integrity checks (applied at three gates)

The class-index chain that must never break:
**model output tensor index *i* → labels.txt line *i* → `ai_models.label_map[label]` → taxon → alert payload `class_index=i`.**

| ID | Check | Gate | How |
|---|---|---|---|
| LM-1 | Output tensor length == number of labels | Upload validation | Parse TFLite output shape; compare to extracted labels; fail the job on mismatch |
| LM-2 | Label **order** matches model class order | Upload validation | Order must come from the model's own metadata (`detection_capabilities` / EI `model_variables.h` when present), never alphabetized; `labels.txt` written in that exact order |
| LM-3 | **Server↔device score parity** on a golden set | Bench, per model version | Run N golden images through (a) TFLite reference runtime server-side and (b) the physical device (SD-card golden folder, read scores back from EXIF); per-class scores must match within an int8-quantization tolerance (±3 pp) **and argmax must agree on 100% of unambiguous images** |
| LM-4 | **Permutation canary** | Bench, once per firmware release | Deliberately deploy a shuffled labels.txt to a test device; the parity harness must FAIL — proves the harness actually detects index/label misalignment |
| LM-5 | Threshold semantics | Bench, per model version | Map OP 16 logit (0–127) ↔ UI percent using the model's output quantization params; verify empirically: a detection scoring X% flips exactly at the compiled threshold |
| LM-6 | EXIF round-trip | Device-in-loop | UserComment present, parses via `backend/app/domain/exif.py` into `user_comment_fields` matching the on-screen scores; MakerNote and DeploymentID unaffected |
| LM-7 | Truncation behaviour | Bench | >4 classes (EXIF_MAX_DYNAMIC_CLASSES) and >20-char labels: verify defined, documented truncation — highest-scoring classes kept, no buffer corruption |
| LM-8 | Provenance completeness | E2E | Every edge observation and alert event resolves to exactly one `ai_models` row via `(firmware_model_id, version_number)`; images from a device running vX while DB says vY must be flagged, not silently mapped |
| LM-9 | Build-flag coupling | Firmware CI | `ENABLE_EXIF_CONFIDENCE` without `USE_PERCENTAGE` must `#error` at compile time (the 2026-06-19 silent-EXIF bug, made structurally impossible) |
| LM-10 | A target class resolves to one observation field | Label map read, edge reflection | `predicts` is `taxon` (a `taxon_id` or `scientific_name`) or `type` (`observation_type` animal, human or vehicle); behaviour is rejected for now. Built in `domain/label_map.py`, see [what a class predicts](../resources/ai-model-pipeline.md#what-a-class-predicts-lm-10) |

## 4.1 Roadmap phases

**V0 — Close the loop (bench, ~now):** ✅ **built on `feat/dual-ai-v0`** — LM-1/2 (`domain/model.py`) and stage 8 edge reflection (`domain/edge_reflection.py`, `ai_origin='edge'`, gated on `FF_EDGE_REFLECT_ENABLED`). Still open: fix `USE_PERCENTAGE` + add LM-9 guard (firmware); land the ww-backend `ai_origin` schema (`feat/dual-ai-v0-schema`); LM-5; the golden-set folder + parity script (LM-3/4/7); and seed/test data (a project model with a real `label_map` + media carrying UserComment NN scores).
**V1 — Deploy paths:** manifest + BLE deploy verified against LM-8; label-mapping made a hard gate before project assignment; model adoption telemetry.
**V2 — E2E dual-AI:** SD upload → EXIF ingest + auto Cloud AI on the same media; annotation panel shows both; agreement report per edge model version (P2).
**V3 — Alerting:** `alert_rules` schema + manifest compilation; Nordic strategies (instant → digest → backoff, in that order); payload v2 + decoder + notifications; conformance rig that replays scripted detection streams into the Nordic over I2C and asserts uplink timing/content.
**V4 — Field pilot:** ≥2 sites, ≥30 days; alert precision via P4 round-trip; battery impact of confirmed uplinks; QA dashboard sign-off.

## 4.2 User stories

**US-01 · Model upload** — *As a model developer, I upload my Edge Impulse int8 ZIP and get a deployable Species Brain or an actionable error.*
AC: Given a valid int8 export, when converted, the model reaches `validated` with `.TFL`+labels stored and LM-1/2 recorded in `processing_log`. Given a float model, the job fails showing the real Vela error. Given a ZIP with `__MACOSX` junk or a renamed `.tflite`, conversion still succeeds.

**US-02 · Label mapping** — *As a model developer, I declare what each class means so the platform can treat predictions as species.*
AC: Every class must be marked target (with taxon) or background before the model can be assigned to any project; classes render in model order (LM-2); saved to `label_map` with org-manager RLS.

**US-03 · Prepare SD Card** — *As a project manager, I generate a Setup Folder containing my project's Species Brain.*
AC: Manifest contains `{fwId}V{ver}.TFL` + `labels.txt` in model order + config + both camera-variant firmwares; projects with no model yield a working manifest with the documented "no Species Brain" notice.

**US-04 · Device runs the brain** — *As a field researcher, after inserting the SD card the camera identifies animals on its own.*
AC: Device boots, loads model+labels from MANIFEST, OP14/15 report the deployed identity; a triggered capture writes EXIF DeploymentID, MakerNote, **and UserComment scores** (LM-6); golden-set parity passes (LM-3).

**US-05 · Both AIs on the website** — *As a project manager, I see the Camera AI and Cloud AI results side by side for each image.*
AC: Upload of an SD card yields edge observations (`ai_origin='edge'`, taxon-mapped, backgrounds skipped) and Cloud AI observations on the same media; blanks still recorded as positive blank observations; canonical label follows human > cloud > edge.

**US-06 · Trust dial** — *As a project manager, I see how much to trust each Species Brain version.*
AC: Model page shows agreement vs Cloud AI + human review, per version, with sample counts; below a configurable sample floor it shows "insufficient data", never an unqualified percentage.

**US-07 · Instant alert** — *As a project manager, I get notified the moment a rat is seen with ≥80% confidence.*
AC: Rule (label=rat, instant, 80%) compiles into the manifest; a qualifying detection produces a confirmed port-10 uplink within one radio cycle; the notification names species, confidence, device, and time; sub-threshold detections produce no uplink; two detections 30 s apart produce one uplink with `count=2`.

**US-08 · Daily digest** — *As a project manager, I get one evening summary instead of pings.*
AC: Detections accumulate per class across sleep cycles; one digest at the configured hour (±radio latency) with per-class counts + max confidence; zero-detection days send nothing; failed send retries next heartbeat before reset.

**US-09 · Smart Back-off** — *As a project manager monitoring a trap line, I hear immediately about a new incursion but am not spammed while it persists.*
AC (conformance rig, scripted stream): first detection → alert stage 0 immediately; continuous presence → alerts at +5 min/+30 min/+2 h/+12 h each carrying suppressed count; gap > clear window → all-clear sent and next detection alerts instantly; device reboot mid-sequence → documented reset to IDLE.

**US-10 · Alert verification** — *As a data scientist, I know which alerts were real.*
AC: After SD upload, each alert event links to media in its time window and is marked confirmed/refuted by Cloud AI + review; per-rule alert precision visible; unmatched alerts flagged for investigation, not dropped.

**US-11 · Retraining flywheel** — *As a model developer, I export reviewed images to improve my next version.*
AC: Export of human-reviewed media (+labels, + Wildlife Brain cluster-confirmed labels) in an Edge Impulse-ingestible layout; new upload lands as vN+1 in the same family; US-06 comparison across versions.

**US-12 · Fleet visibility** — *As a platform engineer, I can audit what every device is running.*
AC: Device heartbeats/BLE stats surface OP14/15; mismatch with the project's assigned model shows an "update available" state in app + website; LM-8 flag on provenance mismatches.

---

## Adoption checklist (first five moves)

1. Adopt §1 names; sweep UI strings + the two lifecycle docs; add the glossary guide.
2. Fix `USE_PERCENTAGE` + LM-9 `#error` guard in Himax firmware (unblocks everything east of the device).
3. Build stage 8 (edge observations + `ai_origin` column) — backend schema change via `db:change`, then website ingest.
4. Land LM-1/2/5 checks in the convert job + the golden-set parity harness.
5. Take §3 to both firmware owners (I2C message extension + CONFIG.TXT lines are cross-repo contracts) before any backend work on `alert_rules`.
