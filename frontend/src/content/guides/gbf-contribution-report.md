---
title: Reporting your contribution to the GBF targets
description: How the GBF contribution report counts invasive and threatened species, monitoring effort and participation, and what it does not claim
category: Analysis
order: 40
updated: 2026-10-25
---

The GBF contribution report shows funders and councils what your projects add to the [Kunming-Montreal Global Biodiversity Framework](https://www.cbd.int/gbf/targets) (GBF). Open **Toolkit**, pick your projects in the header, choose a period (it starts at the last 12 months) and select **Calculate**. **Download CSV** gives one figure per row, and **Download JSON** gives the whole report as the website computed it.

> The figures are site-level evidence from wherever your cameras are. They count towards the targets, but they are not the GBF's national indicators, which each country reports for itself.

## What each tile shows

| Target | Figures |
|---|---|
| 6, invasive alien species | For each invasive species: detections, the rate, the sites with it out of the sites monitored, a monthly trend, and the sites where it was detected for the first time |
| 4, threatened species | The same for each threatened species, and how many were recorded |
| 20, monitoring effort | Cameras, sites, camera-nights, photos taken, photos checked by AI and photos reviewed by people |
| 21, open data | Not reported yet |
| 22, participation | How many people reviewed photos, as a count, never names |

A tile shows the three species with the highest rate; the downloads have them all. Each tile's title links to the target's text.

## How it's calculated

- **Period.** Whole days in UTC, the last day included. A photo counts in the period it was taken, and a photo with no date counts in none.
- **Photo verdict.** Each photo counts once, with the label its card shows on the Annotations page, so a person's label wins over the AI's. Empty photos, and deleted photos and labels, don't count.
- **Detection.** One visit by a species, not one photo. Photos of the same species at the same deployment, each taken no more than 30 minutes after the one before, count as one detection.
- **Camera-nights.** For each deployment, the days between its start and its end (or now, while it is still out) that fall in the period, added up. This assumes the camera worked the whole time, so a flat battery or a full card makes the effort look bigger than it was.
- **Rate.** Detections per 100 camera-nights, so periods and projects of different sizes compare fairly. The monthly trend divides each month's detections by that month's camera-nights.
- **Sites.** A site is a deployment. It is monitored when the deployment overlaps the period or has photos in it. The sites with a species are the deployments with at least one detection of it.
- **First detections.** Deployments where the species was detected in the period and in no earlier photo at that deployment.
- **Invasive and threatened.** From the website's species list. Invasive means introduced or invasive in New Zealand; the list covers New Zealand only. Threatened means Vulnerable, Endangered or Critically Endangered on the IUCN Red List. A species can be both, and then it counts under both targets.
- **Reviewed photos and people.** A photo is reviewed when a person added or confirmed a label on it. The people are the different reviewers of the period's photos.

The report carries a method version (`gbf-1`), so a downloaded report says how it was computed.

## Reading the CSV

The first rows give the report, the period, the projects, the method version and the caveat above. The table under them has five columns: `target`, `subject` (the species' scientific name, empty for a figure about the whole report), `metric`, `month` (empty for the whole period) and `value`. Rates are the `rate_per_100_camera_nights` rows.
