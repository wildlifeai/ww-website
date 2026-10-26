---
title: Tuning motion detection with the live view
description: Watch a camera's live picture over its USB cable, see where it detects motion and whether that would wake it, set its motion settings and type console commands
category: Setup
order: 15
updated: 2026-10-26
---

The **Motion live view** in the Toolkit is a bench tool for a Wildlife Watcher camera on its USB
cable, the website's version of the firmware's `live_view.py`. It shows the live picture with the
motion grid over it and whether that would wake the camera, and lets you change the motion
settings, type console commands and save frames and the session log.

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
   port. Next time **Reconnect** opens the same port without asking.
3. Wait for the picture, usually within 20 seconds. After it is switched on or reset, the camera
   takes no pictures until it has slept once, so the page sends it to sleep and it wakes itself
   10 seconds later. That wake takes its own pictures before the page can switch saving off, so
   **one photo is saved to the SD card** each time you connect after a reset.
4. If the camera does not answer, it is asleep: press its reset button.

The page starts in **Free run**: bursts of 10 pictures, one after another.

## Controls

| Control | What it does |
|---|---|
| Free run | Takes pictures continuously, a burst of 10 at a time |
| Stop | Lets the current burst finish and takes no more; nothing is saved to the card |
| One image | Takes a single picture, after the current burst |
| Save frame | Downloads the last picture as the camera sent it; tick **with the grid** for a second copy with the grid and the wake verdict drawn on it |
| Frame interval | Time between pictures, from the next one. With motion detection on, the black and white camera (HM0360) follows the motion interval, 1 second when that is 0; the colour camera (RP3) takes one every 100 ms |
| Motion detection | Shows the grid, the WAKE lamp and the motion lines. Off, the picture is plain live video, every 20 ms on the HM0360 |
| Keep awake | On by default. Untick it to let the camera sleep once it has been idle for a minute, to test a wake: a wake shows in the log and Free run starts again |

## Reading the motion grid

- The picture is split into 16 by 16 cells. A cell outlined in red has motion. **Grid rows**
  switches to 15 rows, what the sensor's datasheet gives, to compare; motion beyond row 15 is
  counted separately.
- The **WAKE** lamp turns red when **more** cells have motion than the wake threshold: in the
  field, that picture would have woken the camera.
- Beside it: `motion` (cells with motion), `threshold` (for the preview pictures, and in brackets
  for the pictures it checks while asleep), `wake`, and `wakes`, how many pictures since Free run
  would have woken it.
- The sensor smooths motion over several pictures. Hold still for three or four pictures before
  you judge where the cells are.
- On the colour camera the grid comes from the separate motion sensor, with its own lens and
  position, so it is only a rough guide.
- Asleep, the camera checks smaller pictures, so a real sleep test is still the final check.

## The motion settings

Each change is saved to the camera's SD card at once and used in the field. The page reads it
back, so a value the camera refuses does not stay on screen.

| Setting | What it does | Range |
|---|---|---|
| Sensitivity (op 17) | How much a cell has to change to count as motion | Low, Medium, High |
| Wake threshold (op 33) | Wake when **more** than this many cells show motion | 1 to 255, default 4 |
| MD interval (op 11) | Time between motion checks while asleep; 0 turns motion detection off | 0 to 1997 ms |

## Console and Device

- **Console**: type any console command (`getop`, `setop`, `camreg`, `vcm`, `status` ...) and
  press Enter; it is typed when the camera is between pictures. The quick commands are the same as
  `live_view.py`'s. The log shows the latest lines, each picture as one line; **Save log**
  downloads the whole session with times.
- **Device**: the camera type, firmware build and branch, the slot it started from, the SD card
  and the op params the page reads. The build, branch and slot come from the boot banner, so
  reset the camera to see them.

## Checking a placement

1. Set the camera where it will stay, with nothing moving in front of it.
2. Watch for a minute. Cells that light with no animal there, such as moving leaves, grass or
   shadows, would wake the camera for nothing.
3. Move or angle the camera to keep them out, or raise the threshold or lower the sensitivity
   until the lamp stays off.
4. Move something the size of your animal where you expect animals, and check the lamp lights.

> **TODO (team):** recommended settings and placement advice for common sites (foliage, shadows,
> background), once worked out with the live view.

## Disconnecting

Press **Disconnect** before you unplug the cable. The page lets the current pictures finish, turns
the stream off, puts back the two sleep settings it changed as they were (the timelapse wake,
op 7, and how long the camera stays awake, op 8) and sends the camera to sleep. Your motion
settings stay.

> Without **Disconnect** the camera keeps the live view's settings: it wakes 10 seconds after
> every sleep, takes pictures and stays awake a minute, which drains the battery. Reconnect and
> press **Disconnect**, then check both settings before you deploy it.
