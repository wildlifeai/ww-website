---
title: Tuning motion detection with the live view
description: Watch a camera's live picture over its USB cable, see where it detects motion and whether that would wake it, and set its motion settings
category: Setup
order: 15
updated: 2026-10-26
---

The **Motion live view** in the Toolkit shows a Wildlife Watcher camera's live picture with its
motion grid over it. Use it at the site to see what the camera counts as motion, and whether that
would wake it, before you leave it to run.

## What you need

- **Chrome or Edge** on a Windows, macOS or Linux computer. Firefox, Safari and phones cannot
  talk to a USB cable from a web page.
- The camera's USB serial cable for the AI board's console. Close any other program using it
  (TeraTerm, `live_view.py`, the ship-check app): only one can hold it.
- AI firmware that sends the motion grid. If the page says the pictures carry none, update it.

> **TODO (team):** name the first AI firmware release with the motion grid (Seeed PR #276).

## Connecting

1. Plug the cable into the camera and the computer.
2. Open **Motion live view** in the Toolkit, choose **Connect to a camera** and pick the cable's
   port.
3. Wait for the picture, usually within 20 seconds. After it is switched on or reset, the camera
   takes no pictures until it has slept once, so the page sends it to sleep and it wakes itself
   10 seconds later.
4. If the camera does not answer, it is asleep: reset it, or switch it off and on. If it stays
   asleep after its first sleep, wake it from the app (send `AI ver`).

A picture comes about once a second on the colour camera (RP3). The black and white camera
(HM0360) spaces them by the motion interval, as it does in the field: about one every 2 seconds.

## Reading the motion grid

- The picture is split into 16 by 16 cells. A cell outlined in red has motion.
- The **WAKE** lamp turns red when **more** cells have motion than the wake threshold: in the
  field, that picture would have woken the camera.
- The sensor smooths motion over several pictures. Hold still for three or four pictures before
  you judge where the cells are.
- On the colour camera the grid comes from the separate motion sensor, with its own lens and
  position, so it is only a rough guide.
- Asleep, the camera checks smaller pictures, so a real sleep test is still the final check.

## The settings

Each change is saved to the camera's SD card at once and used in the field. The page reads it
back, so a value the camera refuses does not stay on screen.

| Setting | What it does | Range |
|---|---|---|
| Sensitivity (op 17) | How much a cell has to change to count as motion | Low, Medium, High |
| Wake threshold (op 33) | Wake when **more** than this many cells show motion | 1 to 255, default 4 |
| Motion interval (op 11) | Time between motion checks while asleep; 0 turns motion detection off | 0 to 1997 ms |

## Checking a placement

1. Set the camera where it will stay, with nothing moving in front of it.
2. Watch for a minute. Cells that light with no animal there, such as moving leaves, grass or
   shadows, would wake the camera for nothing.
3. Move or angle the camera to keep them out, or raise the threshold or lower the sensitivity
   until the lamp stays off.
4. Move something the size of your animal where you expect animals, and check the lamp lights.

> **TODO (team):** recommended settings and placement advice for common sites (foliage, shadows,
> background), once worked out with the live view.

## Stopping

Press **Stop** before you unplug the cable. The page lets the current pictures finish, turns the
stream off, puts back the two sleep settings it changed (the timelapse wake, op 7, and how long the
camera stays awake, op 8) and sends the camera to sleep. Your motion settings stay.

> Without **Stop** the camera keeps the live view's settings: it wakes 10 seconds after every
> sleep, takes pictures and stays awake a minute, which drains the battery. Connect again and
> press **Stop**, then check both settings before you deploy it.
