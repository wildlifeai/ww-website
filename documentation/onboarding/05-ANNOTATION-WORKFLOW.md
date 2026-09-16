# 05 — Annotation Workflow

How reviewers validate AI labels and create new ones in the web app. This is the surface most users
spend their time in.

## Entry point: the Annotations tab

`/annotations` → `AnnotationsPage` → `MediaBrowser`. The page has **no title text** — the
highlighted nav tab already signals where you are.

### The Ribbon (Microsoft-Word-style command bar)

`MediaBrowser` and `InsightsPage` use a shared, branded `Ribbon` primitive
(`components/ui/Ribbon.tsx`):

- **Menu bar** — a leaf brand mark + ribbon tabs + a live status slot.
- **Ribbon body** — grouped categories, each with controls and a small caption.
- **Dialog launcher** — the ⤢ corner arrow opens advanced settings (Word's group launcher).

Annotations ribbon: a **Filter** tab (Deployment · Species · Status · Annotator + a *Refine* group
whose launcher opens the advanced modal for date range / day-night) and a **View** tab (thumbnail
size S/M/L). Insights uses its four sub-tabs (Projects · Deployments · Map · Reports) as the menu bar.

### The grid

A paginated thumbnail grid (`.range()` + `count:'exact'`, 100/page) with a **StatusBadge** overlay
per image derived from `review_status` (`lib/observations.ts`):

| Badge | Meaning |
|-------|---------|
| ✓ Reviewed (green) | a human validated the label |
| AI (teal) | AI-produced label (incl. `blank`) |
| ⧗ Processing (grey) | no observations yet — still working through the pipeline |
| ✕ Issue (red) | explicit pipeline error (reserved; see `StatusBadge.tsx`) |

### Selection actions

Click selects, double-click opens. Once something is selected the **Actions** menu
(`components/data/MediaBulkActions.tsx`) offers: *Label as…* (one human observation on every
selected image), *Find similar images* (single selection, Wildlife Brain), *Upload to iNaturalist*,
*Remove images* (soft delete with undo), *Run AI (re-classify)* and *Create species ID model…*.
The last one trains a Camera AI model (a Species Brain) from the selected labelled images through
the Edge Impulse recipe (`TrainModelModal.tsx`, `POST /api/models/train`, behind
`FF_MODEL_TRAINING_ENABLED`); camera-produced labels are never used as training data. See
[species-brain-training-spec](../development%20reports/species-brain-training-spec.md).

## The full-screen labeling modal

Selecting a photo opens a **full-screen modal** (`MediaDetail.tsx`):

- **Left** — the image at up to 92vh with **bounding-box overlays** and draw/redraw/delete; ‹/›
  arrows and ←/→ keys step between images; Esc cancels a draw or closes; click the backdrop to close.
- **Right** — a panel with the **observation cards**, file metadata, and **EXIF** (from
  `media.exif_metadata`).

### Reviewer actions (per observation)

| Action | Effect |
|--------|--------|
| **✓ Confirm** | Accept the AI label → `review_status='human_reviewed'`, sets `reviewer_id`; auto-advances |
| **✕ Blank** | False trigger → `observation_type='blank'`, clears species; auto-advances |
| **Correct** | Change species via the taxon-validated `SpeciesPicker` (writes `taxon_id`) |
| **▭ Box / Redraw / ✕** | Draw, replace, or delete the bounding box (writes the bbox quad) |
| **+ Add Observation** | Create a new fully human-provenanced observation |

> With `FF_PER_CROP_CLASSIFY_ENABLED` on, AI produces **one observation per animal** rather than one
> per image, so a mixed-species frame shows a card (and crop) per detection and `count` is reserved for
> human "N individuals" annotations — see [04-AI-PIPELINE](./04-AI-PIPELINE.md#per-detection-classification-ff_per_crop_classify_enabled).

### SpeciesPicker (taxon validation)

`components/data/SpeciesPicker.tsx` debounces a search over the local `taxa` table **and** the
iNaturalist autocomplete (`/api/inat/taxa/search`). Selecting an iNat result registers its lineage
(`POST /api/inat/taxa`) and returns a real `taxon_id`; it falls back to local-only when iNat is
feature-flagged off. Every selection writes a real `taxon_id`, never a free-text string.

## Review provenance (the important invariant)

Every human create/edit flows through **`frontend/src/lib/observations.ts`**:

- `humanCreateFields()` — new rows get `source_type='human'`, `review_status='human_reviewed'`,
  `classification_method='human'`, `annotator_id`, `reviewer_id`.
- `humanReviewFields()` — edits/confirms advance `review_status='human_reviewed'` and set
  `reviewer_id`, **without** clobbering the original `source_type` (an AI row becomes "AI proposed,
  human verified").
- `isHumanReviewed()` / `isAiLabel()` — the single contract the badges, KPIs and colours read from.

This requires the `authenticated` role to hold `INSERT`/`UPDATE` GRANTs on `observations`. If a
confirm fails with `permission denied for table observations`, that's the missing GRANT — see
[03-DATA-AND-SYNC](./03-DATA-AND-SYNC.md).

## Related review surfaces

| Surface | Route | Purpose |
|---------|-------|---------|
| Cluster review | `/clusters/:id` | Bulk-confirm an HDBSCAN cluster to one species |
| Review queue | `/review/:id` | Active-learning order — highest-value images first |
| Dataset health | `/intelligence/:id` | Review funnel + AI-vs-human agreement |

> Event review (`EventReviewPage`) is currently **unrouted** — `/events/:id` was removed from
> `App.tsx`; the page file remains pending re-route-or-delete.

For the model side of these, see [04-AI-PIPELINE](./04-AI-PIPELINE.md). The full design rationale is
in [`development reports/_archive/annotation-pipeline-review.md`](../development%20reports/_archive/annotation-pipeline-review.md)
(archived — point-in-time review).
