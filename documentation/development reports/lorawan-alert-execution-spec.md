# LoRaWAN Device-Alert Execution Spec

> **Status:** ⚠️ Needs a redesign, 2026-11-26, not scheduled. Do not build it as written.
> It was written for the WW500's old LoRaWAN messages. The firmware in
> [ww-hardware#81](https://github.com/wildlifeai/ww-hardware/pull/81) replaces them with one
> tag-value message of counts and health
> ([payload description](https://github.com/wildlifeai/ww-hardware/blob/7a7e8c9546ccd895160a6422b7bfcb6ef7814794/_Documentation/development%20reports/2026-11-06_lorawanPing/WW500_LoRaWAN_payloads.md)),
> with no model identity, class index or score, and passes settings from a downlink on FPort 3
> to the AI processor, which may change how alert rules reach the camera. The *alert logic*
> (modes, ownership split, fair-use) is designed in
> [dual-layer-ai-architecture-proposal §3](./dual-layer-ai-architecture-proposal.md); the
> schema (`device_alert_rules`) has landed (ww-backend `dual_ai_v0`).

Repos: **website** (`ww-website`), **backend** (`ww-backend`), **Himax firmware**
(`Seeed_Grove_Vision_AI_Module_V2`), **Nordic firmware** (`ww-hardware`).

## What exists today

- **Design:** proposal §3: three modes (`instant` / `digest` / `backoff`), the
  Nordic-owns-alert-logic split, LoRaWAN fair-use (confirmed instant, unconfirmed
  digest). The legacy TrapNZ payload it kept goes with ww-hardware#81, which is not
  released (the field firmware still sends it); trap.nz forwarding moves to the backend's `lorawan-ingest`
  ([ww-backend#373](https://github.com/wildlifeai/ww-backend/issues/373)).
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
- keep `threshold_pct` a percent: OP 16 is a percent from 0 to 100 with
  [Seeed PR 289](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/pull/289),
  as [ww-mobile-app#524](https://github.com/wildlifeai/ww-mobile-app/pull/524) writes it
  (see the AI-ARCHITECTURE "contracts" table);
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

Extend the uplink payload to carry **(model identity, class_index, score, mode)** for
the alert. The payload is now the tag-value message of ww-hardware#81, counts and
health only. The legacy heartbeat/sprung frames go with ww-hardware#81, which is not released:
`lorawan-ingest` still decodes the 0x50 ping report of the firmware in the field. trap.nz
forwarding moves to `lorawan-ingest` ([ww-backend#373](https://github.com/wildlifeai/ww-backend/issues/373)).

### 3. Forward (class_index, score) over I2C (Himax firmware, Seeed repo)
Today the Himax forwards only OP counters to the Nordic. Add a small I2C message so
that, per positive inference, it reports **(class_index, score)** — the input the
Nordic needs to evaluate rules. The message-type enums must stay in lockstep across
the two firmware repos. The per-class score it needs is computed: `USE_PERCENTAGE` is on in
the firmware's `main` since 2026-07-08 (Seeed commit `fb66bafd`).

### 4. Decode the uplink → alerts (ww-backend)
Extend the LoRaWAN webhook decoder to recognise the new alert frame, resolve
(device → deployment, class_index → taxon via the model's `label_map`), and write a
**`conservation_alerts`** row + fire the existing **`notification_rules`** delivery.
Each alert is *provisional* — proposal P4: when the SD card is later uploaded, match
alert timestamps to media and mark alerts **confirmed / refuted** by Cloud AI + human
review (retrospective per-rule precision).

## Sequencing & dependencies

1. **Op 16 as a percent**
   ([Seeed PR 289](https://github.com/wildlifeai/Seeed_Grove_Vision_AI_Module_V2/pull/289), open)
   lands first, so a rule's `threshold_pct` and the camera's threshold mean the same thing.
   `USE_PERCENTAGE`, which piece 3's score needs, is on in firmware `main` since 2026-07-08.
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
