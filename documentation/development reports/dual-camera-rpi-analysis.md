# Analysis — Dual Camera (HM0360 night / Raspberry Pi color day) + BMP/JPEG dual-write

> **Status:** 📋 Analysis, not scheduled (2026-11-26). The WW500 took the one-camera-per-board route:
> a Raspberry Pi (RP3) or an HM0360 variant, both firmware images in the A/B slots, which Prepare SD
> card packs (`backend/app/domain/manifest.py`, `_fetch_himax_firmware_pair`). Runtime day/night
> switching between two sensors is not built; it is the firmware's to decide, and the website needs
> nothing for it.

**Question:** can we use a Raspberry Pi camera as the **main** sensor (better, colour, daytime) and
alternate with the **HM0360** (mono, IR, night) by day/night? And how does that interact with the
same-frame BMP+JPEG dual-write? Is there an easy way to do the main-camera change?

## Key finding: the WE2 SDK already drives Raspberry Pi camera sensors

The Himax WE2 (HX6538) image path uses a swappable `cis_sensor/` driver. The Seeed SDK ships drivers
for **exactly the Raspberry Pi camera sensors**, not just the HM0360:

| Driver in SDK | Sensor | = Raspberry Pi camera | Colour | IR |
|---|---|---|---|---|
| `cis_hm0360` | HM0360 | — (current) | **mono** | **yes** (IR-sensitive) |
| `cis_ov5647` | OV5647 | **Pi Camera v1** | colour | IR-cut (no IR) |
| `cis_imx219` | IMX219 | **Pi Camera v2** | colour | IR-cut |
| `cis_imx708` | IMX708 | **Pi Camera v3** | colour | IR-cut |
| `cis_imx477` | IMX477 | **Pi HQ Camera** | colour | IR-cut |

So "use a Pi camera" is **not** a from-scratch port — the WE2 can already talk to these sensors, and
the day/night rationale checks out: the standard (non-NoIR) Pi modules have an IR-cut filter, so they
give good daytime colour but can't see the IR flash at night; the HM0360 is mono + IR for night.

## Concrete firmware findings (this codebase)

Three things in the current `ww500_md` firmware that make the answer specific:

1. **Sensor access is abstracted through Himax `sensordplib` + `hx_drv_cis`** (the CIS camera
   interface), with the actual sensor chosen by which `cis_sensor/cis_*` driver is compiled. Swapping
   the *driver* is the SDK's intended extension point; the friction is the **app code around it**
   (below), not the data-path API.
2. **The camera sits on a configurable I2C slave address** — `pca9574.c` calls
   `hx_drv_cis_get_slaveID` / `set_slaveID` to retarget the I2C bus and restore it. So multiple I2C
   devices on the CIS bus is already a solved pattern. **But** the PCA9574 is a *generic I/O expander*
   (the comment even notes it's the WAT200 irrigation chip), **not** a camera mux — it does **not**
   switch the high-speed MIPI/parallel video lanes. So I2C addressing alone does not let two sensors
   stream; the video path still needs a mux or sequential re-init (see "hard" below).
3. **A day/night signal already exists — no new sensor needed.** The firmware computes auto-exposure
   stats per frame (`gain.aeMean`, `gain.aeConverged`, written into the EXIF MakerNote). `aeMean` is
   average frame brightness: low `aeMean` at max gain/exposure ⇒ night ⇒ HM0360 + IR; bright ⇒ day ⇒
   Pi colour. The day/night decision can ride this existing value (optionally hysteresis + the RTC),
   so that part is genuinely easy.

## What's easy, what's not

**Single-sensor swap (moderate, not trivial).** Building the `ww500_md` app against, say, `cis_ov5647`
instead of `cis_hm0360` would give a colour daytime camera. But the app is **HM0360-coupled** beyond
the driver:
- **Motion detection.** Wake/trigger uses the HM0360's *on-sensor hardware MD* (`hm0360_md.c`,
  `hm0360_md_setMode`, interrupt at reg 0x2065). Pi-camera sensors have **no** hardware MD — daytime
  triggering would need software MD or an external PIR.
- **GRAY8 assumptions.** `prepareBmpFile()` writes a fixed 1078-byte **grayscale** header; the raw
  buffer is treated as 8-bit mono. A colour sensor needs an RGB/Bayer BMP path.
- These are real but bounded changes.

**Dual-camera, day/night switching (the actual ask — hard).** Two sensors on one device needs:
- **Hardware: a MIPI-CSI mux.** The WE2 has a single CSI-2 receiver; two sensors can't stream at once.
  You need a MIPI mux (or a board that muxes), plus mounting + wiring for both modules. This is the
  biggest blocker and is purely hardware.
- **Firmware: runtime sensor switching.** The SDK's model is *one sensor per build*. Day/night
  switching means re-initialising the cisdp/sensor driver (and the ISP/JPEG config) on transition —
  not something the SDK does out of the box. Both `cis_hm0360` and a `cis_ov5647/imx219` driver would
  be compiled in, with a runtime selector + mux control + a day/night decision (ambient-light reading,
  RTC, or a lux threshold).
- **Power/throughput.** Pi sensors are much higher resolution/bandwidth than the HM0360; capture time,
  RAM buffers, JPEG sizes, and SD-card/upload volume all grow on the day path.

**Verdict:** a *single* swap to a Pi colour sensor is moderate firmware work (driver already exists;
adapt MD + colour BMP). The *dual-camera day/night* design is a genuine hardware+firmware project
(MIPI mux + runtime switching), **not** a config change. The enabling piece — Pi-sensor support — is
already there, which is the hard 80%; the remaining 20% (mux + switching + colour BMP) is the work.

## Interaction with the BMP/JPEG dual-write (#1)

The two features are **orthogonal axes** and compose cleanly, with one firmware caveat:

| Axis | What it varies |
|---|---|
| Dual-**write** (#1) | the *same* frame saved as raw BMP **and** hardware JPEG |
| Dual-**camera** | *which sensor* captures, chosen by day/night |

For whichever sensor is active you can still dual-write BMP+JPEG of its frame. The only catch is
**colour**:
- **Night / HM0360:** GRAY8 BMP + (grayscale) JPEG — exactly as today.
- **Day / Pi sensor:** the raw is **colour** (RGB/Bayer), so `prepareBmpFile()` needs a 24-bit RGB BMP
  header variant, and the WE2 ISP/JPEG must be in colour mode (the SDK's colour-sensor drivers already
  configure that).
- **Website: no change needed.** `to_jpeg()` already does `Image.convert("RGB")`, so it re-compresses
  a colour BMP just as happily as a grayscale one. EXIF/binding/pipeline are format- and
  colour-agnostic. So the dual-write #1 spec is unaffected on our side — only the firmware's BMP writer
  gains a colour branch.

## Recommended sequencing (lowest risk first)

1. **Prove the Pi-sensor path on its own** — a `ww500_md` build with `cis_ov5647` (or imx219), daytime
   only, software/PIR trigger. Confirms colour capture + JPEG + (colour) BMP end-to-end into the
   website. Low risk; no mux.
2. **Decide the day/night hardware** — MIPI mux + dual mount, *or* two boards. This is the gating
   hardware decision; everything else depends on it.
3. **Runtime switching firmware** — both drivers compiled in, day/night selector, cisdp re-init, colour
   BMP branch. Pair with the #1 dual-write so each captured frame (from whichever sensor) yields a
   raw+JPEG pair with matching filename stems.
4. **Mobile + website**: the mobile app already has the pattern for a capture toggle (the new
   "Record JPEG only" switch) — a future "Camera: Auto day/night / Colour / IR" selector would write a
   new op-parameter once firmware supports it. The website needs nothing for colour.

## Open questions for the device engineer

- Does the **WW500 carrier board** physically route the WE2 CSI to a connector a Pi module can use, and
  is there room/power for a mux + second sensor?
- Pi **v1 (OV5647)** vs **v2 (IMX219)** vs **v3 (IMX708)** — which best matches the WW500's lens/optics
  and the WE2's supported lane/clock config?
- Day/night **trigger** on the colour path — software MD on the Pi stream, keep the HM0360 as the
  always-on MD wake (it stays powered for motion, Pi powers up for the colour shot), or an external PIR?
  (The **day/night *decision*** itself can reuse the existing `gain.aeMean` brightness — no new part.)
- Is there room on the **CIS bus / a board GPIO** (the PCA9574 is already there for I/O) to drive a
  MIPI mux select + per-sensor power/reset, or is sequential power-cycle-and-re-init fast enough
  between day and night transitions (which happen ~twice a day, so re-init latency is a non-issue)?
- Confirm the WE2 **hardware JPEG encoder** handles the Pi sensor's resolution within the capture-time
  and RAM budget.
