---
title: Grouping look-alikes with the Wildlife Brain
description: How the Wildlife Brain groups visually similar animals so you can review large datasets far faster — clusters, find-similar, and the map
category: Analysis
order: 30
updated: 2026-11-27
---

Once your photos are on the website and the [Cloud AI](/guides/camera-ai-and-cloud-ai) has identified species, you still have to *review* the results — and a busy camera can produce tens of thousands of photos. The **Wildlife Brain** is the tool that makes that review fast.

## What it does

The Wildlife Brain looks at how the animals in your photos **look**, and groups visually similar ones together. It works from an AI "fingerprint" of each animal, so it can tell that two photos show the same kind of critter even when the file names, times, and locations are completely different.

Importantly, it does **not** name species — that's the Cloud AI's job. The Wildlife Brain is a **curation tool**: it organises your data so you can confirm, correct, and explore it in a fraction of the time.

## Three ways you'll use it

- **Clusters**: the Wildlife Brain sorts your animals into groups of look-alikes. Open a cluster, check it's all one species, and **confirm the whole group at once** instead of labelling photo by photo. Mixed group? Split off the odd ones. A group whose photos carry different species is marked **Mixed species** (a broader name such as *Rattus* beside *Rattus rattus* does not count), and confirming it asks first, saying which labels would change. Photos a person already labelled keep their label.
- **Find similar** — from any one animal, jump straight to every other photo that looks like it. Great for pulling together all the sightings of the same individual or the same species.
- **Map** — a visual scatter (a "UMAP") of your entire dataset, where similar animals sit near each other. Handy for spotting patterns, outliers, and anything the automatic labels missed.

## Needs a second look

Where label checks are switched on, the **Review** tab on **Media** offers **Needs a second look**: photos whose label looks wrong next to the photos that look like them, mistakes a person or the AI may have made. Each card says why:

- **Looks like X, labelled Y**: the photos most like it mostly carry another species.
- **Unlike other Y**: it is far from every other photo of its species.
- **Could be X**: it carries a broader name (*Rattus*) among photos with a narrower one (*Rattus rattus*). Never a mistake, shown last.

Labels people confirmed come first. Move **Sensitivity** right to see more, left for only the clearest. **Often confused** lists the species pairs mixed up most; pick one to see both species' photos. If the label is right, choose **Checked, it's right** and the photo leaves the list until its label or fingerprint changes; if it is wrong, correct it and its flag goes at once. A small badge marks these photos in the grid too. The checks run again after each Wildlife Brain run and every night.

## How to run it

On **Media**, pick one deployment in the **Filter** tab, then open the **Review** tab and choose **Run Wildlife Brain** (once it has run, the same place offers **Review clusters** and **UMAP**). It processes in the background (you can keep working and track it in **Processing history**); when it finishes, your clusters, the map, and *find similar* are ready. It also runs by itself after an upload with **Run AI analysis after upload** ticked, as it is by default.

> The Wildlife Brain needs images that are already on the website, so run it after your photos have uploaded and the Cloud AI has processed them.

## The three tools, together

| Tool | Where | What it gives you |
|---|---|---|
| **Camera AI** (your project's *Species Brain*) | on the camera, in the field | an instant flag for your target species |
| **Cloud AI** | website, after upload | accurate species identification |
| **Wildlife Brain** | website, on demand | groups of look-alikes to review fast |

Camera AI and Cloud AI answer *"what is it?"*; the Wildlife Brain answers *"what else looks like this?"* — and a person always has the final say. For how the first two work together, see [How Wildlife Watcher's AIs work together](/guides/camera-ai-and-cloud-ai).
