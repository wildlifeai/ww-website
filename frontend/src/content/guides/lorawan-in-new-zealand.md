---
title: Connecting your Wildlife Watcher over LoRaWAN in New Zealand
description: What LoRaWAN gives you, and every step to get your cameras reporting in, from coverage and a gateway to the project setting in the app and your first check-in
category: Setup
order: 20
updated: 2026-11-27
---

Your Wildlife Watcher can report home over **LoRaWAN**, a long-range, very
low-power radio network. This guide explains what that gives you, then walks
you through setting it up in Aotearoa New Zealand, step by step.

> **Early access:** LoRaWAN support is rolling out now and is being
> field-tested with early-access groups. Talk to us at
> [wildlife.ai](https://wildlife.ai/contact/) before planning a deployment
> around it.

## What the camera sends (and what it doesn't)

- **Check-ins**: a tiny status message with the camera's detection counts,
  battery, SD card space, temperature and health (any fault, and whether it
  has restarted). The mobile app sets how often when you deploy the camera,
  at present every 12 hours. Cameras on older firmware send battery and
  detection counts only, with no health.
- **Straight away when something happens**: when the on-device AI counts a
  detection, or the camera's health changes, it sends a message at once
  rather than waiting for the next check-in. If more happens before then,
  the next check-in carries it. Detections show on the Realtime page as
  **Detection +1** (or more). The website does not send you a notification
  for a detection.
- **Not photos.** Images are far too big for LoRaWAN: they stay on the SD
  card and reach the cloud when you collect the card or sync with the mobile
  app. LoRaWAN is the "is my camera alive, and did it see something?"
  channel.

The camera needs **no internet and no SIM card**: it only needs to be within
radio range of a LoRaWAN **gateway**. The gateway is the part that needs
internet. Realistic range in NZ terrain: several kilometres with clear line
of sight from a well-placed gateway, but dense bush eats the signal, so plan
for hundreds of metres to about 2 km in real bush, and mount the gateway as
high as you can.

Radio legalities: LoRaWAN in NZ uses the 915–928 MHz band, which is
licence-free under RSM's General User Radio Licence. There is nothing to
apply for. Wildlife Watchers ship configured for the **AS923-1** frequency
plan. If you run your own network on a different plan, tell us before we
ship your cameras.

## Setting it up, step by step

1. **Coverage**: find a gateway in range of your site, or set up your own.
2. **Your project**: turn on **LoRaWAN Required** in the mobile app.
3. **Deploy and test**: start monitoring and run the **LoRaWAN Signal** test.
4. **Check**: see the camera's first check-in on the website's Realtime page.

Your cameras need no setting up for LoRaWAN: wildlife.ai registers every
Wildlife Watcher on [The Things Network](https://www.thethingsnetwork.org)
(TTN) before it ships, and forwards its messages to your project. Any TTN
gateway in range carries them.

## Step 1: Coverage

```
Is there a LoRaWAN gateway on The Things Network in range of your site?
│
├─ YES, or a TTN community covers your area ───────────────► Step 2
│
├─ NO, and we're comfortable setting up a gateway ─────────► 1a
│
├─ NO / not sure / we'd rather it just worked ─────────────► 1b
│
└─ We run our own network server (ChirpStack, our own TTN) ► 1c
```

The honest coverage picture: there is no nationwide public LoRaWAN in NZ you
can count on at a conservation site. TTN has active communities in the main
centres, and some regions have community networks (for example IoT
Taranaki), which are worth checking, but assume a rural site has nothing
until proven otherwise.

### 1a: Set up your own gateway

You do **not** need to run any server software: a gateway on the free The
Things Network is enough. These steps are for the Seeed **SenseCAP M2**
(AS923 model), the gateway wildlife.ai tests against, around NZ$150. Other
gateways need the same settings in their own menus.

**Choose the spot.** Height beats power. Put the gateway, or its antenna, as
high as you can with the clearest view towards your cameras. For a powered
site (house, woolshed, hut with solar) the indoor M2 is fine. For a remote
site, budget for a weatherproof outdoor gateway with solar power and a 4G
router, or ask us (1b).

**Connect it to the internet.**

1. Power it on. Hold its button for 5 seconds, until the blue light
   flashes slowly: it now offers a Wi-Fi network called `SenseCAP_` and six
   characters, password `12345678`.
2. Join that Wi-Fi network with a phone or laptop and open
   `http://192.168.168.1` in a browser. Sign in with the username and
   password on the gateway's label.
3. Give it internet in one of these ways, then **Save and Apply**:
   - **Ethernet**: plug in a network cable. Nothing to set.
   - **Wi-Fi**: **Network** → **Wireless** → **Scan**, choose your network,
     enter its password, **Submit**.
   - **4G** (4G model): put a Nano-SIM in, then **Network** → **Cellular**
     and enter your mobile provider's APN.
4. The top light turns solid green when it is online.

**Set its radio plan.** **LoRa** → **Channel Plan**: choose the **AS923**
region and the **AS923-1** plan, then **Save & Apply**. It has to match the
cameras, or the gateway does not hear them.

**Register it on The Things Network.**

1. Create a free account at [thethingsnetwork.org](https://www.thethingsnetwork.org),
   and open the console on the **Australia 1 (au1)** cluster:
   [au1.cloud.thethings.network/console](https://au1.cloud.thethings.network/console).
2. **Gateways** → **Register gateway**. Enter the **Gateway EUI** printed on
   the gateway's label, and choose the frequency plan **Asia 920-923 MHz
   (used by TTN Australia)**. Leave **Require authenticated connection**
   unticked. **Register gateway**.

**Point the gateway at The Things Network.** In the gateway's page at
`http://192.168.168.1` (or its address on your network): **LoRa** →
**LoRa Network**, mode **Packet Forwarder**, server address
`au1.cloud.thethings.network`, up and down ports both `1700`.
**Save & Apply**.

**Check.** Within a few minutes your gateway's page in the TTN console says
**Connected**, and its Live data starts to show traffic. A slowly blinking
red light on the gateway means it cannot reach The Things Network: check its
internet and the server address.

Then go to Step 2.

### 1b: wildlife.ai sets it up with you

The default for most community groups: we make the radio part invisible.

- **A pre-configured gateway kit**: indoor or solar/4G outdoor. Plug it
  into power and internet (or insert the SIM) and your site is live. We
  monitor gateway health remotely.
- **Joining existing networks**: if your region already has coverage (a
  TTN community, a council network), we handle the registration and
  plumbing with the operator instead.

What we ask from you: power and internet (or a 4G plan) for the gateway, and
a conversation about your site's terrain before we ship, so we get the
coverage right the first time. **Contact us at
[wildlife.ai](https://wildlife.ai/contact/) to get started.** Then go to
Step 2.

### 1c: You run your own network server

For councils, iwi/hapū environmental units, universities and trapping
networks that already operate ChirpStack or their own TTN application, and
want their cameras on it.

**What you need from us:**

- The device's **DevEUI, JoinEUI (AppEUI) and AppKey**, for an OTAA join,
  LoRaWAN 1.0.3, Class A.
- Frequency plan **AS923-1** (other plans, e.g. AU915, are possible as a
  firmware build option; ask before deployment, as it cannot be changed in
  the field).
- Uplinks arrive on **FPort 2**. Your network server passes them on
  unchanged and wildlife.ai decodes them, so you need no payload decoder.

**What we need from you:** get in touch, and we connect your network server
to Wildlife Watcher with you. It forwards each uplink to wildlife.ai by
webhook (TTN v3 and ChirpStack v4 both work). The webhook's access token
stays with wildlife.ai, so we set it up together rather than sending it to
you. Then go to Step 2.

## Step 2: Turn on LoRaWAN in your project

A project admin does this once, in the Wildlife Watcher app:

1. **Projects** → open the project → **Edit Project**. For a new project,
   **New Project**.
2. Open **Advanced Project Settings** and tick **LoRaWAN Required**.
3. Save.

From then on, every camera you deploy in the project has its LoRaWAN turned
on, with its check-in period set, when you start monitoring. A camera
deployed in a project without it has its LoRaWAN turned off.

## Step 3: Deploy the camera and test it

Deploy as usual: connect to the camera in the app and open **Start
Monitoring**. In a project that requires LoRaWAN, the screen has a
**LoRaWAN Signal** row. Tap it and the camera tries to send one test
message:

| The app says | What it means | What to do |
|---|---|---|
| **Sent** | The camera is on the network and sent a test message | Nothing. Carry on |
| **LoRaWAN is off** | This camera has not been on LoRaWAN yet | Nothing: starting monitoring turns it on |
| **Not joined yet** | The camera has not reached the network through a gateway | Check the gateway is online and in range, then **Test Again** |
| **Busy** | The camera's radio was already sending | **Test Again** in a minute |
| **No answer** | The camera did not answer the app | Check the app is still connected to the camera, then **Test Again** |

Then **start monitoring**. On current firmware, a camera that was off joins
the network within a minute or so of being deployed, and sends its first
check-in straight after.

## Step 4: Check it on the Realtime page

On the website, open **Realtime** and select the project. The camera shows
under **Cameras** as **Awaiting** until its first message arrives, then with
its health, battery and SD card space, and its messages appear under
**Messages**. What each part means is in the next section.

**If nothing arrives:**

- The app's **LoRaWAN Signal** test said **Not joined yet**: the camera is
  out of range of a gateway, or the gateway is offline. Check the gateway's
  light and its **Connected** status in the TTN console, raise the gateway or
  move it closer, and test again.
- The camera is not listed at all: it is not deployed in a project you have
  selected, or the project is not one you belong to.
- The camera was deployed before the project had **LoRaWAN Required**
  ticked, so its LoRaWAN is off. Tick it, then deploy the camera again with
  the app.
- With your own network server (1c): check it forwards to Wildlife Watcher,
  and talk to us.

Later, a camera that goes **Silent** has sent nothing for three check-in
periods: its gateway may have lost power or internet, its battery may be
flat, or it may have been moved out of range.

## The Realtime page

Once your cameras send LoRaWAN messages, the **Realtime** tab shows the cameras deployed in the
projects you have selected, and the ones collected today.

- **The map.** Tap or click a camera to pick it: it gets a ring on the map and its row is
  highlighted in the Cameras table. Tap it again to clear it. The page has no photos: they stay
  on the SD card until you collect the camera and upload them.
- **Cameras** lists each camera once, with its newest message.
  - **Health** is the camera's state, with its first reason beside it; hover over it for the
    rest. Silent, Fault and Warning cameras are also listed, with every reason, under **need
    attention** above the map.
    - **Fault**: its main camera, its HM0360 camera or its AI processor failed, or its SD card
      is missing.
    - **Warning**: a low battery, an SD card 10% free or less, another self-test problem, or,
      since its previous health report, an unexpected restart or a time its AI processor did
      not respond.
    - **OK**: its last health report found nothing wrong. **Checking in**: it sends messages,
      but its firmware sends no health report.
    - **Silent**: no message for three check-in periods, counted from its deployment if it has
      sent none. A camera deployed without its check-in period recorded is **Stale** after 24
      hours with no message and Silent after 3 days.
    - **LoRaWAN off**: deployed with its LoRaWAN check-ins turned off. **Awaiting**: no message
      yet. **Finished**: its deployment ended today.
  - **Battery** is in volts, or a percentage from older firmware. Battery and **SD free** turn
    red when the camera's health report finds the battery low or the SD card 10% free or less.
  - **Firmware** is the version the camera was deployed with.
  - **Detections 48h** is what the camera's own AI counted in the last 48 hours.
- **Messages** lists the messages, newest first:
  - **Check-in**, or **Detection +2** when the camera counted 2 detections since its previous
    message. A camera's first count after it is deployed has nothing to compare with, so that
    message shows as a Check-in.
  - **Battery** in volts, or as a percentage from older firmware.
  - **SD free** is how much of the SD card is still free; a dash means the camera could not
    tell.
  - It opens with the newest 50 messages. **Load older** adds 50 more, up to 500.
  - Messages that arrive while the page is open stay lightly highlighted.
- **Live** by the title means new messages appear as they arrive. **Connecting** means the
  page is reconnecting; it reads everything again once it is back, and when you return to the
  tab.

## Quick technical reference

| Item | Value |
|---|---|
| LoRaWAN version / class | 1.0.3, Class A, OTAA |
| Frequency plan (NZ default) | AS923-1; on The Things Network, **Asia 920-923 MHz (used by TTN Australia)**; licence-free under RSM GURL |
| The Things Network cluster | Australia 1, `au1.cloud.thethings.network` |
| Uplink port | FPort 2 |
| Check-in content | detection counts, battery, SD card space, temperature, health (self-test, last restart and why, restarts, AI processor failures); older firmware: battery and detection counts |
| Check-in interval | set by the mobile app when it deploys the camera, at present every 12 hours |
| Messages between check-ins | at once on a new detection or a change in health, at most one of each per check-in interval |
| Gateway tested by wildlife.ai | Seeed SenseCAP M2 (AS923), Packet Forwarder to TTN |
