# LoRaWAN Device-Alert Execution Spec

> **Status:** 📋 Active spec — implementation not started. The *alert logic* (modes,
> ownership split, fair-use) is designed in
> [dual-layer-ai-architecture-proposal §3](./dual-layer-ai-architecture-proposal.md);
> the schema (`device_alert_rules`) has landed (ww-backend `dual_ai_v0`). This doc is
> the concrete **build breakdown** across the four repos — the "alerting doc pair" that
> §3 marked *to create*. Gated behind the firmware `USE_PERCENTAGE` fix (see
> [dual-ai-production-rollout §4](../resources/dual-ai-production-rollout.md)).

Repos: **website** (`ww-website`), **backend** (`ww-backend`), **Himax firmware**
(`Seeed_Grove_Vision_AI_Module_V2`), **Nordic firmware** (`ww-hardware`).

## What exists today

- **Design:** proposal §3 — three modes (`instant` / `digest` / `backoff`), the
  Nordic-owns-alert-logic split, LoRaWAN fair-use (confirmed instant, unconfirmed
  digest), and the legacy TrapNZ payload that must keep working.
- **Schema:** `device_alert_rules` (per-project): `model_family_id`, `label`,
  `mode`, `threshold_pct` (1–100), `digest_send_utc`, `backoff_steps_min[]`,
  `clear_window_min`, `enabled`. RLS: project members read, admins manage; service
  role for the manifest job + decoder.
- **Adjacent, already built:** the manifest job (`domain/manifest.py`, writes
  `CONFIG.TXT` OP codes) and `conservation_alerts` (decode target).
- **Ingest:** uplinks arrive only through the **ww-backend `lorawan-ingest` edge
  function**. The website's legacy FastAPI webhook was removed (#319). Piece 4 below
  extends the **edge function**.

## What's missing — four pieces

```
 device_alert_rules ─(1 compile)─► SD manifest alert block ─(2 execute)─► LoRaWAN uplink
   (ww-backend)         website           on the card         Nordic fw        │
                                                                   ▲            ▼
                                              (Himax→Nordic I2C)   │      (4 decode) backend
                                              class_index+score ───┘      → conservation_alerts
                                                    (3 Himax fw)             + notifications
```

### 1. Compile rules → device config (ww-website manifest job)
Extend `generate_manifest` so that, for the project's deployed model version, each
`enabled` `device_alert_rules` row is compiled into an **alert block** written to the
SD manifest (a new `ALERTS.TXT`, or an OP-code range in `CONFIG.TXT` — Nordic-owned
format, coordinate). At compile time:
- resolve `label` → the model's **output class index** (via `ai_models.label_map` /
  `detection_capabilities` order — the same index the device uses);
- convert `threshold_pct` → **device logit units** using the model's output
  quantization (OP 16 is a logit 0–127, *not* a percent — see the AI-ARCHITECTURE
  "contracts" table);
- emit `mode`, `digest_send_utc`, `backoff_steps_min[]`, `clear_window_min` verbatim.
- Validate: reject a rule whose `label` isn't a `role='target'` label in the deployed
  version's `label_map` (the schema notes this is enforced here, not by FK).

### 2. Execute the strategy (Nordic firmware, `ww-hardware`)
The nRF52832 reads the alert block and runs the state machine per rule:
- **instant** — on a qualifying detection, send a **confirmed** uplink (retry ≤ 3),
  rate-limited per fair-use;
- **digest** — accumulate qualifying detections, send one **unconfirmed** summary at
  `digest_send_utc`;
- **backoff** — alert immediately, then suppress for `backoff_steps_min[i]` after each
  successive alert while the target persists; reset the sequence after
  `clear_window_min` with no qualifying detection.

Extend the uplink payload beyond the legacy TrapNZ counters to carry
**(model identity, class_index, score, mode)** for the alert — keep the legacy
heartbeat/sprung frames working (TrapNZ integration).

### 3. Forward (class_index, score) over I2C (Himax firmware, Seeed repo)
Today the Himax forwards only OP counters to the Nordic. Add a small I2C message so
that, per positive inference, it reports **(class_index, score)** — the input the
Nordic needs to evaluate rules. The message-type enums must stay in lockstep across
the two firmware repos. **Depends on `USE_PERCENTAGE`** (branch
`feat/exif-confidence-enable`) since that's what computes the per-class score.

### 4. Decode the uplink → alerts (ww-backend)
Extend the LoRaWAN webhook decoder to recognise the new alert frame, resolve
(device → deployment, class_index → taxon via the model's `label_map`), and write a
**`conservation_alerts`** row + fire the existing **`notification_rules`** delivery.
Each alert is *provisional* — proposal P4: when the SD card is later uploaded, match
alert timestamps to media and mark alerts **confirmed / refuted** by Cloud AI + human
review (retrospective per-rule precision).

## Sequencing & dependencies

1. **Firmware `USE_PERCENTAGE`** ships first (piece 3 needs the score; nothing edge
   works without it).
2. **I2C message contract** agreed across Himax + Nordic repos (pieces 2 ↔ 3).
3. **Manifest compile** (1) + **decoder** (4) can be built in parallel on the website/
   backend side against a mocked frame, then integrated once the firmware emits real
   uplinks.
4. A **project-settings UI** to author `device_alert_rules` (CRUD) is a website
   prerequisite for real use (not covered here — small form over the existing table).

## Open questions

- **Config carrier:** new `ALERTS.TXT` vs. an OP-code range in `CONFIG.TXT`? Nordic
  owns the format — decide with the `ww-hardware` team.
- **Uplink frame layout + LoRaWAN fair-use budget** per region (owned by Nordic).
- **De-dupe** vs. the cloud pipeline: an alert and its later Cloud-AI observation are
  the same event — P4's timestamp match must avoid double-counting.
