---
title: Preparing an SD card
description: What the Toolkit's setup folder holds, why it is faster than updating over Bluetooth, and how to put it on the camera.
category: Setup
order: 5
updated: 2026-10-21
---

## Why an SD card

The mobile app can update a camera over Bluetooth, but the camera software and the AI model are
large files and take a long time to send that way. The Toolkit's **Prepare SD card** builds the
same files into one setup folder. Copy it to the SD card, put the card in the camera, and the
camera installs them when it starts, with no wait.

## What goes in the folder

- **The camera software (Himax firmware)** you pick. The folder holds both camera variants when
  they are available: the version you picked, and the latest active version of the other variant
  (RP3 colour or HM0360 night and infrared), so one card suits either camera.
- **Your project's AI model** (its Species Brain), the model that identifies animals on the
  camera. You choose it in the project's settings. A project with no model gets the camera
  software and settings only, and you can add a model later.

The model file on the card is named after its firmware model id and version number
(`<id>V<version>.TFL`, operation parameters 14 and 15 on the camera). A model with no model
family or version number cannot go on a camera; an organisation manager sets them when the model
is uploaded.

Only projects you can work in (project member or above) are offered.

## After you download it

1. Unzip the downloaded folder.
2. Copy the `MANIFEST` folder, as it is, to the root of the SD card.
3. Put the card in the camera and power it on.
4. Connect to the camera from the mobile app and start monitoring.
