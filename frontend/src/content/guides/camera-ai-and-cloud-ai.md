---
title: How Wildlife Watcher's AIs work together
description: What the Camera AI on your device and the Cloud AI on the website each do, why one photo can have two opinions, why a person still checks them, and how the Wildlife Brain groups look-alikes
category: Analysis
order: 20
updated: 2026-11-26
---

Wildlife Watcher identifies animals in two places — once on the camera itself, and again on the website after you upload — and then a third tool, the **Wildlife Brain**, groups the look-alikes to speed up your review. They are different tools with different jobs, and your photos benefit from all of them.

## Camera AI — on the device

**Camera AI** is the camera's ability to identify animals on its own, in the field, with no internet. The specific model that does this is called your project's **Species Brain** — a small, efficient model (for example, a "rat" vs "not rat" detector). You choose it in your project settings, and it is installed on the camera when you prepare an SD card (the Toolkit's **Prepare SD Card** step) or update the camera from the mobile app.

- It works in the field, instantly, on every photo the camera takes.
- It is trained for **your project's target species**, not for every animal.
- Its results are saved inside each photo, so they travel with the image to the website, where they show as **Camera AI** beside the website's own results (see below).

> **Tip:** a project without a Species Brain still takes photos normally — it just won't identify animals until you add one in the project settings.

## Cloud AI — on the website

After you upload photos, the website automatically runs a much larger identification system on powerful servers. Its results can take from a few minutes to a few hours to appear. Think of it as the thorough second opinion:

- It first finds the animals in each photo, then identifies the species.
- It knows thousands of species, not just your project's targets.
- It marks empty photos as **blank**, so you don't review them one by one.

Because it has far more computing power than the camera, the Cloud AI result is the more accurate of the two — it is the benchmark your team reviews against.

## Why both?

| | Camera AI | Cloud AI |
|---|---|---|
| Where it runs | On the camera, offline | On the website, after upload |
| Speed | Immediate, in the field | Minutes to a few hours after upload |
| Species it knows | Your project's targets | Thousands of species |
| Best at | Fast local detection | Accurate identification |

The two are complementary: the Camera AI reacts in the field the moment something is seen, and the Cloud AI double-checks everything once the photos reach the website.

## Two results on one photo

On the website, a photo can carry several opinions. In the photo grid, a teal badge with a chip icon means an AI labelled the photo, and a green badge with a tick means a person has reviewed it. Open a photo to see where each result came from:

- **Camera AI**, with the model's name: what your camera's Species Brain decided in the field.
- The name of a Cloud AI model, for example SpeciesNet: what that model decided after upload.
- **Consensus**: the website's models weighed together, when there is one.
- The reviewer's email: a person has checked it.

The photo's label, under **Verdict**, is one of these, and the others are listed under **model opinions**. The website picks the label in this order:

1. A person's label, once someone has reviewed the photo.
2. Without one, a **Consensus** result, when there is one, decides whether the photo is empty, and the species name comes from the first model result the website saved.
3. With neither, the first result the website saved.

The first result saved is usually the **Camera AI**'s, because the website reads it from the photo before the Cloud AI runs. So until a person reviews a photo, its label can be the camera's answer even where the Cloud AI disagrees: open the photo to compare them. The results aren't a conflict to resolve. They are independent checks that make it easy to trust the ones that agree and focus your attention on the ones that don't.

## Why a person still checks the AI

Neither AI is right every time, so the website treats every AI label as a suggestion. A photo only shows **Reviewed** once a person has confirmed or corrected it.

A 2026 study in the *Biodiversity Data Journal* shows why. It asked a general-purpose AI model, the kind behind chat assistants, to name the species in 10,000 photos of Colombian amphibians, birds, fish, mammals and reptiles. That is not the AI Wildlife Watcher uses, and the photos were nature photos shared online rather than camera-trap photos, but its lessons apply to any AI that names species:

- **The fewer photos of a species there are online, the worse the AI does.** For species with fewer online photos than nine in ten others, the study estimated the AI would name the exact species only 6% to 12% of the time in most groups, and 41% of the time for mammals.
- **Recently described species are hard too.** Accuracy fell sharply for species that scientists named in recent years, and in one case the AI gave an out-of-date name.
- **Mistakes are not random, and some are far off.** Some answers were not even in the right group of animals, most often for fish and reptiles. In another example, a camera-trap AI suggested a species whose range does not include Colombia.

The hard cases can be the ones that matter most. A wrong label on a rare species can hide a real sighting, or create a false one that sends a survey to the wrong place. The study recommends treating an AI's answer as a suggestion rather than a confirmed record, letting the AI say when it is unsure, checking each name against the species known from the area, and sending rare or newly described species to an expert.

The small animals Wildlife Watcher is built to watch, such as skinks, frogs and invertebrates, are often the kind of species with few photos for an AI to learn from. So for now, a person makes the final call. To review a photo, open it on the **Media** page and choose **Confirm** if the AI is right, **Label** to pick the right species, or **Blank** if there is nothing in it. The [Wildlife Brain](/guides/wildlife-brain) lets you confirm a whole group of look-alikes at once.

> **Source:** Paez Salamanca GN (2026) [Is conservation AI ready? Quantifying the challenges of automated species identification in Colombia](https://doi.org/10.3897/BDJ.14.e208137). *Biodiversity Data Journal* 14: e208137.

## Wildlife Brain — grouping look-alikes

After the Cloud AI has identified species, the **Wildlife Brain** does something different: it looks at how the animals *look* and groups visually similar ones together. It doesn't name species — it's a curation tool that helps you review a big dataset far faster:

- **Clusters** — confirm a whole group of look-alike animals in one go instead of one photo at a time.
- **Find similar** — from one animal, jump straight to others that look like it.
- **Map (UMAP)** — a visual scatter of your whole dataset to spot patterns and outliers.

It runs after an upload when **Run AI analysis after upload** is ticked, as it is by default, and you can run it yourself from **Media**: pick one deployment in the **Filter** tab, then choose **Run Wildlife Brain** in the **Review** tab. See [Grouping look-alikes with the Wildlife Brain](/guides/wildlife-brain) for the full walkthrough. (It's a separate tool from your camera's *Species Brain*: the Species Brain identifies on the device; the Wildlife Brain groups on the website.)

## Camera AI detections over LoRaWAN

A camera connected over long-range radio (LoRaWAN, in early access) also sends a short message when its Camera AI detects something, so the detection shows on the website's **Realtime** page before you collect the SD card. The website does not send a notification for it. See [Connecting your Wildlife Watcher over LoRaWAN](/guides/lorawan-in-new-zealand).

For help setting up your camera, see [Focusing the camera](/guides/focusing-the-camera). For common questions about the AI, see the [FAQ](/faq).
