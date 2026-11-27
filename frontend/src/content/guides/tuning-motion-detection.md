---
title: Tuning motion detection with the live view
description: Watch a camera's live picture over its USB cable, see where it detects motion and whether that would wake it, set its motion settings and type console commands
category: Setup
order: 15
updated: 2026-11-27
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
- AI firmware that sends the motion grid. No released AI firmware sends it yet, so ask wildlife.ai
  for a build that does. Without it, the page says the pictures carry no motion grid.

## Connecting

1. Plug the cable into the camera and the computer.
2. Open **Motion live view** in the Toolkit, choose **Connect to a camera** and pick the cable's
   port. Next time **Reconnect** opens the same port without asking, also after you unplug the
   cable and plug it back in.
3. If the page says **The camera is asleep**, press the camera's RESET button. A sleeping camera
   hears nothing, and connecting does not wake it.
4. If it says **No answer: this may be the other port**, you picked the Bluetooth chip's cable.
   Disconnect, connect again and pick the other port: a bench with two cables of the same make
   lists both under the same name.
5. Wait for the picture, about 20 seconds after a reset. The steps show over the picture: the
   camera restarts (twice, as the Bluetooth chip restarts the AI board too), sleeps once, wakes
   10 seconds later, then sends its first picture. It takes no pictures until it has slept once,
   so the page sends it to sleep and sets it to wake itself. That wake takes its own pictures
   before the page can switch saving off, so **one photo is saved to the SD card** each time you
   connect after a reset. The page then puts the 10 second wake back as it was.
6. Keep the tab in front until the picture shows. The camera goes back to sleep a second after it
   starts unless the page types to it, and Chrome slows a tab in the background. If the steps stop
   moving, the page says **Still starting**: press RESET again.

The page starts in **Free run**: bursts of 10 pictures, one after another.

## Controls

| Control | What it does |
|---|---|
| Free run | Takes pictures continuously, a burst of 10 at a time |
| Stop | Lets the current burst finish and takes no more; nothing is saved to the card |
| One image | Takes a single picture, after the current burst |
| Save frame | Downloads the last picture as the camera sent it; tick **with the grid** for a second copy with the grid and the wake verdict drawn on it |
| Frame interval | Time between pictures, from the next one, 20 ms to 10 seconds. With the motion grid shown, the black and white camera (HM0360) follows the motion interval, 1 second when that is 0; the colour camera (RP3) takes one every 100 ms |
| Show motion grid | Shows the grid, the WAKE lamp and the motion lines. Off, the picture is plain live video, every 20 ms on the HM0360. It changes nothing on the camera |
| Keep awake | On by default. Untick it to test a wake: it stops Free run, whose pictures keep the camera awake, and the camera sleeps after a minute without commands. Each wake shows its own pictures and what woke it (motion, or its timer when the timelapse is set), then the camera sleeps again. With it ticked, a wake starts Free run again |

If the pictures stop while Free run is on, the page says **No picture for** so many seconds: the
camera is switched off, or its cable is loose.

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

Each change is saved to the camera's SD card at once and used in the field. The new value shows
once the camera confirms it; if the camera refuses it or does not answer, the page says so and
shows the camera's own value again. Without an SD card a change lasts only until the camera
restarts, and the Device tab shows the card as **none, or unreadable**.

| Setting | What it does | Range |
|---|---|---|
| Sensitivity (op 17) | How much a cell has to change to count as motion | Low, Medium, High |
| Wake threshold (op 33) | Wake when **more** than this many cells show motion | 1 to 255, default 4 |
| MD interval (op 11) | Time between motion checks while asleep; 0 turns motion detection off | 0 to 1997 ms |

## Console and Device

- **Console**: type any console command (`getop`, `setop`, `camreg`, `vcm`, `status` ...) and
  press Enter; it is typed when the camera is between pictures. A command this camera does not
  have gets "Command not recognised" in the log (`vcm` is for the colour camera only). The quick
  commands are the same as `live_view.py`'s. The log shows the latest lines, each picture as one
  line; **Save log** downloads the whole session with times.
- **Device**: the camera type, firmware build and branch, the slot it started from and what woke
  it last, the SD card and how full it is, and the op params the page reads. The build, branch
  and slot come from the boot banner, so reset the camera to see them.

If the page says **This firmware has no live preview**, the camera's AI firmware is older than the
live view: the page takes no pictures, so none are saved to the card.

## Checking a placement

1. Set the camera where it will stay, with nothing moving in front of it.
2. Watch for a minute. Cells that light with no animal there, such as moving leaves, grass or
   shadows, would wake the camera for nothing.
3. Move or angle the camera to keep them out, or raise the threshold or lower the sensitivity
   until the lamp stays off.
4. Move something the size of your animal where you expect animals, and check the lamp lights.

## Disconnecting

Press **Disconnect** before you unplug the cable. The page lets the current pictures finish, turns
the stream off, puts back the two sleep settings it changed as they were (the timelapse wake,
op 7, and how long the camera stays awake, op 8) and sends the camera to sleep. Your motion
settings stay.

> Without **Disconnect** the camera keeps the live view's settings: it stays awake a minute after
> every wake (op 8), and if the session ended during its first sleep it also wakes every 10
> seconds and takes pictures (op 7), which drains the battery. Reconnect and press **Disconnect**,
> then check both settings before you deploy it.
