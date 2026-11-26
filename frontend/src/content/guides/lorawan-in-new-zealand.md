---
title: Connecting your Wildlife Watcher over LoRaWAN in New Zealand
description: What LoRaWAN telemetry gives you, and the three ways to get connected, through an existing network, your own gateway or a wildlife.ai kit
category: Setup
order: 20
updated: 2026-11-26
---

Your Wildlife Watcher can report home over **LoRaWAN**, a long-range, very
low-power radio network. This guide explains what that gives you, and the
three ways to get connected in Aotearoa New Zealand.

> **Early access:** LoRaWAN support is rolling out now and is being
> field-tested with early-access groups. Talk to us at
> [wildlife.ai](https://wildlife.ai/contact/) before planning a deployment
> around it.

## What the camera sends (and what it doesn't)

- **Heartbeats**: a tiny status message with battery level, firmware version
  and how many times the camera has been triggered. The Realtime page shows
  each one as a **Check-in**. The mobile app sets how often when you deploy
  the camera, at present every 12 hours.
- **Detection messages**: when the on-device AI recognises the animal (or
  person) it is watching for, the camera sends a short message straight
  away. It shows on the website's Realtime page as **Detection +1** (or more,
  when the camera counted several) and adds to the camera's **Detections
  48h**. The website does not send you a notification for it.
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

## Which path is yours?

```
Does your site already have LoRaWAN coverage you can use?
│
├─ YES, and we run or manage the network ourselves ───────► Option 1
│
├─ NO, but we're comfortable setting up a gateway ────────► Option 2
│
└─ NO / not sure / we'd rather it just worked ────────────► Option 3
```

## Option 1: You already run a LoRaWAN network

For councils, iwi/hapū environmental units, universities and trapping
networks that already operate The Things Network (TTN), ChirpStack or
similar.

**What you need from us:**

- The device's **DevEUI, JoinEUI (AppEUI) and AppKey**, for an OTAA join,
  LoRaWAN 1.0.x, Class A.
- Frequency plan **AS923-1** (other plans, e.g. AU915, are possible as a
  firmware build option; ask before deployment, as it cannot be changed in
  the field).
- Uplinks arrive on **FPort 2**. Your network server passes them on
  unchanged and wildlife.ai decodes them, so you need no payload decoder.

**What we need from you:** get in touch, and we connect your network server
to Wildlife Watcher with you. It forwards each uplink to wildlife.ai by
webhook (TTN v3 and ChirpStack v4 both work). The webhook's access token
stays with wildlife.ai, so we set it up together rather than sending it to
you. Once that is done and the camera is deployed in your project,
heartbeats and detection messages appear on your project's Realtime page.

**Checklist:** register the device → confirm it joins on your network server
→ connect your network server to Wildlife Watcher with us → deploy the
camera in your project with the mobile app → see its first check-in on the
Realtime page. Done.

## Option 2: Build your own coverage

For groups happy to do a bit of DIY. You do **not** need to run any server
software: a gateway plus the free The Things Network is enough.

The honest coverage picture: there is no nationwide public LoRaWAN in NZ you
can count on at a conservation site. TTN has active communities in the main
centres, and some regions have community networks (for example IoT
Taranaki), which are worth checking, but assume a rural site has nothing
until proven otherwise.

The realistic default is your own gateway:

1. **Buy a gateway.** For a powered site (house, woolshed, hut with solar),
   an indoor gateway like the Seeed SenseCAP M2 (AS923 model) is what
   wildlife.ai tests against, around NZ$150. For remote sites, budget for a
   weatherproof outdoor gateway plus solar power and a 4G router.
2. **Give it internet.** Ethernet or Wi-Fi where available; a 4G SIM
   elsewhere. Remember: only the gateway needs internet, not the cameras.
3. **Point it at The Things Network** (free): register the gateway on the
   TTN AU1 cluster with the frequency plan *Asia 920–923 MHz (AS923-1)*.
   wildlife.ai maintains a step-by-step gateway guide for the SenseCAP M2;
   ask us for it.
4. **Register your cameras** on TTN using the credentials we give you,
   then get in touch and we connect TTN to Wildlife Watcher with you. From
   here everything behaves like Option 1.

> **Siting tip:** height beats power. Get the gateway antenna as high as
> possible with the clearest view towards your cameras. If you are not sure
> a spot is in range, talk to us before you deploy a camera there.

## Option 3: wildlife.ai sets it up with you

The default for most community groups: we make the radio part invisible.

- **Pre-provisioned cameras**: your Wildlife Watchers arrive already
  registered on the network; nothing to type in.
- **A pre-configured gateway kit**: indoor or solar/4G outdoor. Plug it
  into power and internet (or insert the SIM) and your site is live. We
  monitor gateway health remotely.
- **Joining existing networks**: if your region already has coverage (a
  TTN community, a council network), we handle the registration and
  plumbing with the operator instead.
- **The dashboard is included**: your project's Realtime page shows each
  camera's heartbeats (battery, trigger counts) and detection messages.

What we ask from you: power and internet (or a 4G plan) for the gateway, and
a conversation about your site's terrain before we ship, so we get the
coverage right the first time.

**Contact us at [wildlife.ai](https://wildlife.ai/contact/) to get
started.**

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
| LoRaWAN version / class | 1.0.x, Class A, OTAA |
| Frequency plan (NZ default) | AS923-1 (920–923 MHz), licence-free under RSM GURL |
| Uplink port | FPort 2 |
| Heartbeat content | battery %, firmware version, ping interval, total triggers |
| Heartbeat interval | set by the mobile app when it deploys the camera, at present every 12 hours |
| Detection message content | positive-detection counter (sent within seconds of the AI firing, min. 60 s apart) |
| Gateway tested by wildlife.ai | Seeed SenseCAP M2 (AS923) |
