# 05 — Annotation Workflow

How reviewers validate AI labels and create new ones in the web app. This is the surface most users
spend their time in.

## Entry point: the Media tab (`/annotations`)

`/annotations` → `AnnotationsPage` → `MediaBrowser`. The page has **no title text** — the
highlighted nav tab already signals where you are.

### The Ribbon (Microsoft-Word-style command bar)

`MediaBrowser` and `InsightsPage` use a shared, branded `Ribbon` primitive
(`components/ui/Ribbon.tsx`):

- **Menu bar**: ribbon tabs and a live status slot (the header carries the logo).
- **Ribbon body** — grouped categories, each with controls and a small caption.
- **Dialog launcher** — the ⤢ corner arrow opens advanced settings (Word's group launcher).

Annotations ribbon: a **Filter** tab (Deployment, Species, and an *Advanced filters* dialog for
status, type, annotator, AI model, sex, life stage and date range, applied on *Apply*; with one
deployment and the active-learning flag on, an *Order* choice, *Needs review first*) and a
**Group** tab (Group by, Collapse all). The menu bar's status slot holds the pager ("1–100 of N"
with Prev and Next), the Photo / Labels toggle, the thumbnail size and *All on page*. Insights uses its four sub-tabs (Projects · Deployments · Map · Reports) as the menu bar.

### The grid

A paginated thumbnail grid, 100 photos a page. Every filter runs in the media query
(`lib/gridFilters.ts`), so the pager's total is the filtered total, counted once per filter set and
again after a write (`lib/gridPage.ts`); the species, model and annotator lists cover the whole
selection (`lib/distinctValues.ts`). The day/night filter is off until the database can answer it.
Groups split the page: grouping by deployment is offered for 2 to 12 deployments, by cluster for
one (`lib/gridGrouping.ts`), and grouping is not remembered between visits. Each card shows the
photo's verdict (`photoVerdict`, common name first) with one confidence and a **StatusBadge**
derived from `review_status` (`lib/observations.ts`):

| Badge | Meaning |
|-------|---------|
| Reviewed (green) | a human validated the label |
| AI (teal) | AI-produced label (incl. `blank`) |
| none | not labelled yet; the card says "No label" |
| Issue (red) | explicit pipeline error (reserved; see `StatusBadge.tsx`) |

A card with no thumbnail yet shows **Processing…** while its deployment has a queued or running
job, or for 10 minutes after the photo was registered. After that it shows **No thumbnail**, and
one notice above the grid offers **Retry**, which runs the thumbnail backfill for each deployment
with such cards (#208). The grid also
starts that backfill by itself, once per deployment per browser session, the first time it shows a
deployment's **No thumbnail** card, so a deployment whose thumbnails cannot be made does not loop
(`hooks/useAutoRetryThumbnails.ts`, #175). A photo with no rendition and a Google Drive original
has nothing an `<img>` can load, since `/api/media/{id}/image` needs a Bearer header, so the grid
and the viewer never request it (`lib/mediaImageUrl.ts`, #300). The viewer shows the same states
as the card, **Processing…** or **No preview yet** with Retry, and its filmstrip a plain tile. Both
show the user's own copy of a just-uploaded file until its rendition exists. While a job
runs on a deployment in view the grid quietly refetches its page every 30 s, keeping the cards on
screen until the new rows arrive, and it reloads once more when
the job finishes (`hooks/useRefreshWhileBusy.ts`, `hooks/useBusyDeployments.ts`,
`lib/thumbnailRetry.ts`). The pipeline makes thumbnails newest first, the grid's order, four at a
time (#286).

### Selection actions

Only someone who can write to a photo's project (project member or above, `useProjectRoles`) can
select it; a viewer, the demo included, sees no circles, no *All on page* and no Actions. A click
(or Enter) opens a photo, even while others are selected, and the selection survives the viewer.
The circle at a card's bottom-left, Ctrl/Cmd-click or Space selects; Shift-click or Shift+Space
selects the range from the last selected card; the arrow keys move between cards; a group's
*Select* takes the whole group (`lib/cardSelection.ts`, #283). A selection belongs to its page: a
new page or filter starts with none. Once something is selected the sub-bar shows the count, Clear
and the **Actions** menu (`components/data/MediaBulkActions.tsx`): *Label as…*, *Find similar
photos* (single selection, Wildlife Brain), *Upload to iNaturalist* (or a link to connect it on
Toolkit), *Remove photos* (soft delete with Undo), *Run AI again* and, for organisation managers,
*Create species ID model…*. *Label as…* gives each photo one human row, updating the one it has,
and leaves AI rows as the model wrote them (`lib/humanVerdict.ts`, decision 2 of the
[UI audit](../development%20reports/2026-10_ui-audit/README.md)); its toast offers Undo, and a
refused write shows there too. *Create species ID model…* trains a Camera AI model (a Species Brain) from the selected labelled images through
the Edge Impulse recipe (`TrainModelModal.tsx`, `POST /api/models/train`, behind
`FF_MODEL_TRAINING_ENABLED`); camera-produced labels are never used as training data. See
[species-brain-training-spec](../development%20reports/species-brain-training-spec.md).

## The full-screen labeling modal

Clicking a photo opens a **full-screen modal** (`MediaDetail.tsx`; its photo area and filmstrip are
`MediaDetailImage.tsx`):

- **Left** — the image at up to 92vh with **bounding-box overlays** and draw/redraw/delete; ‹/›
  arrows and ←/→ keys step between images; Esc cancels a draw or closes; click the backdrop to close.
- **Right** — a panel with the **observation cards**, file metadata, and **EXIF** (from
  `media.exif_metadata`).

### Reviewer actions (per observation)

| Action | Effect |
|--------|--------|
| **Confirm** (C) | Agree with the photo's verdict, or with the selected row: it becomes your label, one human row (`source_type='human'`, `lib/humanVerdict.ts`), and the viewer moves on. A photo a person already ruled on just moves on |
| **Blank** (B) | False trigger: your label becomes `observation_type='blank'`; moves on |
| **Label** (A) | Pick a species in the taxon-validated `SpeciesPicker` (writes `taxon_id`), with an optional count; it becomes your label. One human species label per photo |
| **Box** | On your label: draw, replace or delete its box |
| **Remove** (Del) | Clears your label's box first, then the label itself. AI rows cannot be removed |

Every action shows Undo, and the AI rows stay as the model wrote them, so "AI said X, a person said Y"
survives (UI audit decision 2). A write RLS refuses (0 rows) reports "Not saved" instead of
reading as saved (#183). A viewer sees none of these actions.

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
| Cluster review | `/clusters/:id` | Bulk-confirm an HDBSCAN cluster to one species; reassign, flag for an expert or publish to iNaturalist the photos selected in a cluster |
| Dataset health | `/intelligence/:id` | Review funnel + AI-vs-human agreement |

Cluster review's *Publish to iNaturalist* calls `POST /api/inat/publish`, so it needs the account
connected in Toolkit and a deployment with a location, and names each encounter by its photos'
current labels ([inaturalist-integration](../development%20reports/inaturalist-integration.md#4-publish-phase-2)).

The Review Queue (`/review/:id`) and Event Review were retired in October 2026 (UI audit decisions 3
and 6): `/review/:id` now opens the deployment's photos in the grid.

For the model side of these, see [04-AI-PIPELINE](./04-AI-PIPELINE.md). The full design rationale is
in [`development reports/_archive/annotation-pipeline-review.md`](../development%20reports/_archive/annotation-pipeline-review.md)
(archived — point-in-time review).
