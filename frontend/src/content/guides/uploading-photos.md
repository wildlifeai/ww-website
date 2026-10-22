---
title: Uploading photos from a camera
description: How the Upload page matches photos to deployments, and what capture sessions are
category: Analysis
order: 5
updated: 2026-10-21
---

Open **Upload** and drop in the `MEDIA` folder from a Wildlife Watcher SD card, or a CamtrapDP package (`.zip`) from another camera-trap tool. You can upload to projects where you are a member or an admin; viewers can look but not add.

## How photos find their deployment

Each camera writes its deployment id into every photo, and the page reads that first. Photos without one (raw `.bmp` frames) go by their card folder, which is named after the first eight characters of the id. Test photos, taken before a deployment was set on the camera, are left out.

Before you upload, **Where these photos will go** lists each deployment the photos claim:

- **matched**: uploads as it is.
- **not in the database** or **no deployment info**: you choose where the photos go.
- **in a project you cannot access** or **can't upload to**: these photos are left out.

## Capture sessions

Photos that match no deployment are grouped into capture sessions: the same camera id or card folder, with no gap over six hours. For each session, assign an existing deployment, create a new one from the photos (under the camera's id, so the phone app's copy lines up), or skip it. Skipped photos are not uploaded.

With many sessions, **Assign all** puts every undecided session into one deployment, and **Create one deployment per session** makes one each (sessions with the same camera id share one).

## After you press Upload

Photos are saved to Google Drive, and **Run AI analysis after upload** (on by default, off for CamtrapDP) runs species detection and identification. The site opens **Media** on the uploaded deployments while a panel in the corner tracks the upload. Keep the tab open until every photo is sent. **Processing history**, in the account menu, keeps each job's log.
