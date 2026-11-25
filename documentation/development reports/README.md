# Development reports

Working records of how the website got here: design proposals, engineering hand-offs,
reviews and the evidence behind decisions. Same convention as `development reports/` in
ww-backend, ww-mobile-app and the firmware repo.

For *what the code does today*, read [`../onboarding/`](../onboarding/) and
[`../resources/`](../resources/) — those track the code. These reports do not.

Three rules keep this simple:

1. **Docs are the record; GitHub issues are the tracker.** Anything still *open* when a
   discussion pauses — a bug found, a decision not yet made, a follow-up — is filed as a
   GitHub issue (they land on the
   [project board](https://github.com/orgs/wildlifeai/projects/3) automatically). A document
   is never the only place an open item lives. File one in 30 seconds with the
   [review-finding template](../../.github/ISSUE_TEMPLATE/review-finding.md).
2. **Every report opens with a one-line `> **Status:**` banner.** It is what tells the next
   reader whether they are looking at a plan, a live hand-off, or history. Keep it true.
3. **Never leave substantive material only in a chat transcript, email or PR comment.** An
   investigation, a review exchange, a diagnosis that took an afternoon: it belongs in a
   report here. This is the failure mode to watch for, because the work is done and the
   finding is real, and it evaporates anyway because it only ever existed in a conversation.

## Writing one

Most reports here are a **single dated file** — `topic-name.md` with a status banner. That is
the right shape for a spec or an analysis. When a discussion grows working files around it
(logs, evidence, scripts), give it a dated folder `YYYY-MM_short-topic/` with a `README.md`
carrying **Status / Outcome / Open items** instead. Append as it evolves — these are the audit
trail, don't rewrite them.

Keep to decisions, contracts, measured numbers, commands and open items; link to the document
that owns a fact instead of restating it (the standard is in
[`.agents/skills/references/documentation.md`](../../.agents/skills/references/documentation.md), Documentation Invariant).

A report whose status is **📋 Proposal** carries, in its status banner, either a link to the
issue that tracks the work or the words "not scheduled". A proposal with neither is how the
decoupled upload pipeline spec sat untracked for two months.

Closing a report (the only ritual):

- [ ] Outcome written — the short "why" a future developer reads instead of the whole thread
- [ ] every remaining open item filed as an issue and linked
- [ ] the linked issues checked that they are *still* open work: one already fixed and merged
      reads as available work and wastes someone's afternoon
- [ ] the docs that track the code updated (`onboarding/`, `resources/`) — a report is not a
      substitute for them
- [ ] status banner changed to `🕰️ Historical snapshot` and the file moved to
      [`_archive/`](_archive/)

`_archive/` is frozen: point-in-time plans and roadmaps kept for the *why*. Never read it as a
description of current behaviour.

## Filing a finding

When a review or an investigation turns up several distinct problems, give each one its own
folder under the thread: `<Letter>_short_name/`, holding an `explanation.md` written in the
four sections of the
[review-finding template](../../.github/ISSUE_TEMPLATE/review-finding.md), whatever
reproduces it (a script, a failing request, a query), and `logs/` for the evidence.

- **Reproduce it before you file it**, and say how in the Evidence section. A finding nobody
  can reproduce on demand is not an issue yet.
- **The issue body is the explanation without its header**, with evidence as permalinks to the
  commit rather than pasted excerpts that drift out of date.
- **When you learn more, edit the explanation and the issue body together.** Never add a
  comment that leaves the next reader reconciling two versions of the same finding.

Worked example, in the firmware repo: `_Documentation/development reports/2026-09-03_capture_bench_findings/`.

## Reports

Ordered roughly by how likely you are to need them. Descriptions live in the
[documentation index](../README.md) — this table is the status board.

| Report | Status |
|---|---|
| [2026-11_label-checks](2026-11_label-checks.md) | 📋 Proposal, decisions taken 2026-11-21: DINOv3 checks for odd-one-out and look-alike labels, not built; #448, #449, #450 |
| [2026-10_aws-reannz-migration](2026-10_aws-reannz-migration/README.md) | 🔧 Active, 2026-11-26: approach B (Lambda, S3, REANNZ batches) is in `dev` behind settings; the `ww-dev` stacks are deployed and nothing points at them. The export link (#462) and the deploy role (#468) need their steps on AWS, the GBF report (#456) is unblocked, and the export leaves out S3 originals (#493); next, the dev site onto `ww-dev` and the 4-hourly crontab |
| [2026-10_ui-audit](2026-10_ui-audit/README.md) | ✅ Built 21 to 25 October 2026 (#381, #389 to #396, follow-ups #399); kept because code and docs cite its numbered decisions |
| [2026-10_e2e-in-ci](2026-10_e2e-in-ci.md) | 🔧 Active, 2026-11-26: items 2 to 6 done; item 1, pull request previews through Cloudflare's branch control, open with no issue |
| [species-brain-training-spec](species-brain-training-spec.md) | 🔧 Active: built behind `FF_MODEL_TRAINING_ENABLED` (#151); first live Edge Impulse run and a load on a camera still to do; training moves to the REANNZ cluster with #463 |
| [2026-09_evidence-pipeline-architecture](2026-09_evidence-pipeline-architecture/README.md) | 📋 Proposal, 2026-09-29: evidence score, consensus row and `media_evidence` contract; items 1 to 3 built in #155; of its section 17, #163, #170, #171, ww-backend#208 and #209 are closed, #164 to #169 open |
| [2026-09_false-negatives-and-vlm-audit](2026-09_false-negatives-and-vlm-audit/README.md) | 🔧 Active. Gemini presence step, labeller and eval script implemented behind `FF_GEMINI_PRESENCE_ENABLED`; prompt v1 stays in production after the v2 benchmark on the labelled set (700 frames since 2026-10-09), v3 measured on a subset, section 6 |
| [2026-09_person-detection-e2e](2026-09_person-detection-e2e/README.md) | 🔧 Active, not scheduled: #140 and #135 are closed and edge reflection types a person as `human`; a step 4 rerun that ends in a Camera AI row is still to do, with no issue |
| [ios-universal-links-team-id](ios-universal-links-team-id.md) | 🔧 Active, 2026-11-26: the fix is applied (`bf27754`, served by wildlifewatcher.ai); the check on a real device and the `7399889N9Z` question are open, with no issue |
| [model-class-semantics](model-class-semantics.md) | ✅ Decided 2026-10-09, v1 built (#135 closed); requiring a `taxon_id` and the GBIF export still open, see its Outcome |
| [ww-backend-schema-handoff](ww-backend-schema-handoff.md) | 📋 Open, 2026-11-26: items 3, 4 and 8 for ww-backend, not yet filed as ww-backend issues; item 1 was wrong and item 7 is done |
| [decoupled-upload-pipeline-spec](decoupled-upload-pipeline-spec.md) | 📋 Proposal, not scheduled: nothing implemented, needs a decision; the AWS migration's S3 uploads change its starting point |
| [lorawan-alert-execution-spec](lorawan-alert-execution-spec.md) | ⚠️ Needs a redesign, 2026-11-26, not scheduled: written for the old LoRaWAN messages, which [ww-hardware#81](https://github.com/wildlifeai/ww-hardware/pull/81) replaces with counts and health only |
| [storage-quota-spec](storage-quota-spec.md) | 📋 Proposal, not scheduled: nothing built; its ww-backend half is item 8 of the schema hand-off; assumes Drive originals |
| [exif-telemetry-firmware-spec](exif-telemetry-firmware-spec.md) | 🔧 Active, firmware hand-off: NN scores reach `UserComment` since 2026-07-08; temperature and battery still to add; website side shipped |
| [dual-camera-rpi-analysis](dual-camera-rpi-analysis.md) | 📋 Analysis, not scheduled: the WW500 ships one camera per board in two variants, both packed by Prepare SD card; switching sensors by day and night is not built, and is the firmware's to decide |
| [inaturalist-integration](inaturalist-integration.md) | 🔧 Built behind `FF_INAT_ENABLED`, off everywhere; the tables are in ww-backend's baseline |
| [dual-layer-ai-architecture-proposal](dual-layer-ai-architecture-proposal.md) | ✅ Adopted — v0 built and merged |
| [gpu-worker-infra-spec](gpu-worker-infra-spec.md) | ✅ Shipped, dev 2026-07-03 and production 2026-07-26; kept as design history; the AWS migration retires the worker at its step 8 |
| [bmp-ingestion-analysis](bmp-ingestion-analysis.md) | ✅ Website side shipped |

Closed reports are in [`_archive/`](_archive/), listed in the [documentation index](../README.md).
