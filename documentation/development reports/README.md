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
| [2026-09_false-negatives-and-vlm-audit](2026-09_false-negatives-and-vlm-audit/README.md) | 📋 Proposal / Analysis — detector false negatives, Wildlife Brain fallbacks, cascaded VLM blank auditing, and iNaturalist constraints |
| [2026-09_person-detection-e2e](2026-09_person-detection-e2e/README.md) | 🔧 Active — model uploaded, transferred and run on hardware with correct labels; website-side reflection blocked on a fresh capture set ([#140](https://github.com/wildlifeai/ww-website/issues/140)) |
| [ios-universal-links-team-id](ios-universal-links-team-id.md) | 🔧 Active — one-value fix identified and verified; iOS password-reset links stay broken until it ships |
| [model-class-semantics](model-class-semantics.md) | 📋 Design decision needed ([#135](https://github.com/wildlifeai/ww-website/issues/135)) — blocks behaviour models and any GBIF export |
| [ww-backend-schema-handoff](ww-backend-schema-handoff.md) | 📋 Open items for the `ww-backend` schema/seed owners |
| [decoupled-upload-pipeline-spec](decoupled-upload-pipeline-spec.md) | 📋 Proposal — nothing implemented; needs a decision |
| [lorawan-alert-execution-spec](lorawan-alert-execution-spec.md) | 📋 Active spec — not started; gated on the firmware `USE_PERCENTAGE` fix |
| [storage-quota-spec](storage-quota-spec.md) | 🔧 Active — needs `ww-backend` schema, then website enforcement |
| [per-crop-classification-spec](per-crop-classification-spec.md) | 🔧 Active — GPU worker prerequisite met; flag-gated rollout |
| [exif-telemetry-firmware-spec](exif-telemetry-firmware-spec.md) | 🔧 Active — firmware hand-off; website side already shipped |
| [dual-camera-rpi-analysis](dual-camera-rpi-analysis.md) | 🔧 Active — firmware/hardware analysis; no website work |
| [inaturalist-integration](inaturalist-integration.md) | ⚠️ Phases 1–4 built, but the `ww-backend` tables were never migrated |
| [dual-layer-ai-architecture-proposal](dual-layer-ai-architecture-proposal.md) | ✅ Adopted — v0 built and merged |
| [gpu-worker-infra-spec](gpu-worker-infra-spec.md) | ✅ Shipped (dev) — retained as design history |
| [bmp-ingestion-analysis](bmp-ingestion-analysis.md) | ✅ Website side shipped |
| [access-test-seed-spec](access-test-seed-spec.md) | ✅ Shipped — seeded in `ww-backend` |
