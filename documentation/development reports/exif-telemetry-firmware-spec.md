# Report — Adding Telemetry (Temperature, Battery, …) to WW500 EXIF

> **Status:** 🔧 Active spec — current engineering hand-off; kept current until shipped.

**Audience:** firmware engineer on `Seeed_Grove_Vision_AI_Module_V2` (WW500 image path) and the
MokoTech nRF/BLE side.
**Goal:** surface device telemetry (temperature first) in each photo's EXIF so the website records and
displays it, with the **smallest possible firmware change**.

## What the website now expects (already shipped)

The website EXIF parser ([`ww-website/backend/app/domain/exif.py`](../../backend/app/domain/exif.py))
was updated to:

1. **Read tags it previously dropped:** `Make` (0x010F), `Model` (0x0110), `MakerNote` (0x927C). These
   already flow into `media.exif_metadata` (jsonb) and show in the image's EXIF panel — no further
   firmware work needed; just confirming they're captured.
2. **Parse the `UserComment` "label: value; …" payload** into structured fields. Recognised keys are
   surfaced as typed columns in `exif_metadata`:

   | UserComment key (case-insensitive) | Becomes | Type |
   |---|---|---|
   | `Temp` / `Temperature` | `temperature_c` | float |
   | `Batt` / `Battery` | `battery_pct` | int |
   | `RSSI` | `lorawan_rssi` | int |
   | `SNR` | `lorawan_snr` | float |

   Every `key: value` token is also preserved in `user_comment_fields` (so on-device NN class
   scores are not discarded by the parser).

   > ⚠️ **Correction (2026-06-19):** the device does **not** currently write the NN scores to
   > `UserComment`. The `UserComment` tag is omitted entirely because the firmware `USE_PERCENTAGE`
   > flag is disabled (while `ENABLE_EXIF_CONFIDENCE` is on), so the confidence producer is compiled
   > out — see the firmware repo (`Seeed_Grove_Vision_AI_Module_V2`, `ww500_md`: `cvapp.h`
   > `USE_PERCENTAGE` / `ENABLE_EXIF_CONFIDENCE`; the write-up lives on the
   > `feat/exif-confidence-enable` branch, not on `main`).
   > The parser is ready; the firmware flag is the blocker. Full flow:
   > [Embedded model lifecycle](../resources/embedded-model-lifecycle.md).
   >
   > **Update (2026-11-26):** no longer true. `USE_PERCENTAGE` is on in the firmware's `main` since
   > 2026-07-08 (Seeed commit `fb66bafd`, `cvapp.h`), so `UserComment` carries the NN scores; the bench
   > run of 2026-09-05 saw them in every frame. Telemetry is still not in it (firmware `dev`,
   > `exif_builder.c`), which is this report's open ask.

**So the only firmware change required to get temperature end-to-end is: include
`Temp: <celsius>;` in the `UserComment` string.** No new EXIF tag, no parser change, no DB migration.

## Where to change it (`image_task.c`)

`build_exif_segment()` already assembles `UserComment` as `"<label>: <value>; "` pairs from the NN
output (around lines 2710–2754). Append telemetry to that same buffer before
`addIFD(TAG_USER_COMMENT, …)`:

```c
// after the NN class-score loop fills user_comment[]…
snprintf(user_comment + offset, EXIF_COMMENT_LENGTH - offset,
         "Temp: %d.%02d; Batt: %d; ", degrees, fraction * 25, battery_pct);
// then the existing addIFD(TAG_USER_COMMENT, …)
```

Watch-outs:
- **`EXIF_COMMENT_LENGTH`** — ensure the buffer has headroom for the extra ~20 bytes; bump it if the
  NN string can already fill it.
- **`IFD0_ENTRY_COUNT`** — only matters if you add a *new tag*. Appending to the existing
  `UserComment` needs **no** entry-count change. If you instead add a dedicated tag (e.g.
  `TAG_TELEMETRY = 0xF201`, mirroring `TAG_DEPLOYMENT_ID = 0xF200`), increment `IFD0_ENTRY_COUNT` and
  add a matching `EXIF_TAGS` entry on the website — but the `UserComment` route is strictly less work.
- `UserComment` is currently only written when `cv_modelLoaded()`. If you want telemetry on **every**
  frame (including blanks / no-model), move the telemetry append outside that guard.

## The cross-MCU catch (temperature lives on the nRF)

Temperature is read on the **nRF/BLE MCU**, not the Himax that writes the EXIF:

```c
// ww-hardware/MokoTech/Workspace/WildlifeWatcher_1/main.c:795
acutetech_utils_get_temperature(&degrees, &fraction);   // currently only NRF_LOG_INFO'd
```

The Himax writes the EXIF and already receives `deployment_id` from the nRF over their existing link.
**Piggyback temperature (and battery %) onto that same nRF→Himax message**, then the Himax has the
values when it builds `UserComment`. This is the only non-trivial part — everything downstream is
done. If the Himax has its own temperature source, use that and skip the cross-MCU hop.

## Recommended payload

Keep it compact and stable so the parser mapping holds:

```
Temp: 14.5; Batt: 87; <existing NN scores…>
```

Other "available and relevant" fields worth adding the same way (all already on the device):
- **Battery %** (`Batt:`) — power-budget tracking per deployment.
- **LoRaWAN `RSSI:` / `SNR:`** — link quality, once LoRaWAN telemetry lands.
- **Firmware version** — you already expose `acutetech_utils_getAppVersion()`; `FW: 1.2.3;` would let
  the website group/triage by firmware (handy given the in-field firmware-update issues in `logs/`).

## Optional follow-up (website side, only if you want to query/chart telemetry)

`temperature_c` etc. already land in `exif_metadata` (jsonb) and display per-image. To **filter or
chart** by temperature, add a real `media.temperature_c` column (a `ww-backend` migration) and copy it
out of `exif_metadata` at media-registration time. Not required for visibility.
