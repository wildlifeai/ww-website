# Review grid audit (MediaBrowser slice)

All paths under `frontend/src/`. `MB` = `components/data/MediaBrowser.tsx`, `MBA` = `components/data/MediaBulkActions.tsx`, `DFO` = `lib/deploymentFilterOptions.ts`, `CSS` = `styles/index.css`.

## 1. What is on screen by default (1280x800, one deployment, 1,000 photos)

Content column is `.container` 1200px max with 1rem gutters (CSS:53-57), so about 1168px of grid under a 56px header (`Ribbon.tsx:63`).

**Above the ribbon**: `ProcessingBanner` only while a job touches a deployment in view (MB:206-254). Nothing else.

**Ribbon menu bar** (sticky, MB:1209; `Ribbon.tsx:167-191`), left to right, all visible without clicking:
1. Leaf brand chip, no label (`Ribbon.tsx:168-171`), decorative.
2. Tab "Filter" (MB:1272) and tab "Group" (MB:1305); clicking the active tab collapses the body (`Ribbon.tsx:153-156`).
3. Stats text: "**1000** media (100 shown) · **N** detections · **N** annotated", plus "labels" in crop view, "no image", "uploading" when non-zero (MB:1214-1221). Hidden below 1180px viewport (CSS:160-162), so it shows at 1280.
4. Photo / Labels two-button toggle (MB:1225-1233).
5. Thumbnail size range slider 80 to 280, default 140 (MB:1234-1238).
6. Bulk bar: "0 selected", "All", "None" (disabled), "Actions" (disabled) (MBA:103-139). Rendered at all times, even with nothing selected.

**Ribbon body, Filter tab** (default): Deployment MultiSelect trigger "All deployments" (MB:1275-1276), Species MultiSelect "All species" (MB:1279-1280), "Advanced filters" button (MB:1288-1299) **and** a corner launcher arrow that opens the same modal (MB:1284-1286, `Ribbon.tsx:201-211`). Each group has an uppercase caption underneath (`Ribbon.tsx:120-123`).

**Sub-bar**: only when grouping is active or an advanced filter is set (MB:1253-1269): a "Grouped by X" chip with clear, and "Clear advanced filters". Because `groupBy` is persisted (MB:304-306, 316), the sub-bar can appear on first load.

**Behind a click**: Group tab = Group by select with 9 options (MB:1308-1319), Similarity slider only in cluster mode (MB:1321-1331), two links only in cluster mode with exactly one deployment (MB:1335-1342). Advanced filters modal = 4 + 2 selects, 2 date inputs, 3 time buttons, Clear all, Apply (MB:1533-1656). Actions menu = Label as, Find similar (single selection only), Upload to iNaturalist, Remove images, Run AI, Create species ID model (MBA:149-218). Each MultiSelect menu = "N selected", Clear, optional search box, checkboxes (`MultiSelect.tsx:106-146`).

**Grid**: flat grid `repeat(auto-fill, minmax(140px, 1fr))`, gap 0.75rem (MB:1461-1467). Inferred at 1168px: 7 columns of about 156px; card = 109px image (MB:939) plus three text lines, about 160px tall; about 24 cards visible of the 100 on the page, 15 rows of scrolling to reach the pager. Each card carries: status badge top-left (MB:1086-1096), iNat dove top-right when present (MB:1099-1103), hover-only select circle bottom-left (MB:968-987, CSS:171-181), species label or "No label"/"Empty", file name, confidence, capture time (MB:1110-1127).

**Below the grid**: pager "««, ‹ Prev, 1–100 of 1000, Next ›, »»" only when total > 100 (MB:1493-1530), bottom only. Banners that can appear between ribbon and grid: iNat result (MB:1349-1359), "Loading media…" (1361), error (1362), green notice (1363-1369), amber not-hosted notice (1375-1388), blue find-similar banner (1396-1410). Six inline banner styles.

## 2. Findings

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| 1 | No role check in the grid. A project_viewer (and the demo) sees select circles, "All", and an Actions menu offering Label, Remove, Run AI, Train, iNat; the bulk label inserts straight into `observations` and RLS failure surfaces as "Error: …". Only the viewer's Remove button is gated (`MediaDetail.tsx:250-255, 492`). | MB:968-987, 1243-1249, 583-613; MBA:149-218; no `canEdit`/`isAdmin` in MB, MBA, MediaGroup, BulkLabelModal (grep) | ROLE | high | Resolve `canEditObservations` (or a project-level `has_project_role`) once for the deployments in view and hide circles, "All" and the Actions menu for viewers; the demo then falls out of the same rule. |
| 2 | Demo gating is a toast after the click: every write action is still listed, and `ClusterLabelAll` has no guard at all, so the demo gets "failed". | MBA:19, 92; MB:45-99, 96 | ROLE | med | Covered by #1; until then wrap ClusterLabelAll in `DemoDisabled`. |
| 3 | iNat "connect" from the grid is a `window.prompt` asking the user to paste an API token. | MB:351-361 | ROLE / SIMPLICITY | med | Replace with a link to Settings. |
| 4 | Species, annotator, model, sex and life-stage options are built from the 100 loaded rows, and those filters run client-side on the page only. With 50,000 photos the species list is whatever the newest 100 contain, and a filter can show 3 of 2,000. | MB:616-653, 670-721 | SCALE | high | Move these filters into the media query (embedded-resource filter or an RPC) and build options from a distinct query over the selection. |
| 5 | Counts mix scopes: "media" is the server total, "detections"/"annotated" are the page; with a client filter set the page can be empty while the pager still says "of 1000" and Next is enabled. The pager text duplicates "(100 shown)". | MB:873-882, 1215-1217, 1430, 1513-1517 | SCALE | high | One scope: server-side filters so `totalCount` reflects them, stats line says "on this page" for page-only numbers, or drop the stats line and keep the pager. |
| 6 | Client filters do not reset the page; only the deployment filter does. | MB:470 (only `filterDeployments`, `deployments`) | SCALE | low | Reset on any filter change once filters are server-side. |
| 7 | `count: 'exact'` over the whole selection on every page turn, every 4 s during an upload and every 30 s while a job runs. | MB:497, 520, 747; `lib/thumbnailRetry.ts:84` | SCALE | med | `count: 'planned'` or fetch the count once per filter set. |
| 8 | Default query lists every deployment id of the selected projects in one `in.(...)`; 100 ids is about 4 KB of URL and grows with projects. | MB:478-480, 498 | SCALE | low | Filter by project through a view/RPC, or cap and warn. |
| 9 | Group by deployment with 100 deployments: the page is the 100 newest photos across all of them, so grouping yields up to 100 groups of 1 to 3, all expanded, re-sorted by size on every page, no collapse-all. | MB:845-846, 867; `MediaGroup.tsx:26` | SCALE / GROUPING | high | Disable deployment grouping above N deployments or group server-side (first 12 per deployment with "show all"); add collapse-all. |
| 10 | Species/sex/life-stage grouping keys on `observations[0]`, not `photoVerdict`, so a card labelled by its human verdict can sit in "(No species)" or the AI's group. | MB:834-840 vs 1020 | GROUPING | med | Key on `photoVerdict(m.observations).labelObs`. |
| 11 | Cluster mode is selectable at any time and posts all active deployment ids; "Label all" and the Review clusters / UMAP links only exist when exactly one deployment is active, with no hint otherwise. With one unfiltered deployment `deploymentId` is undefined (MB:1447), so the confirm succeeds but the per-deployment cluster cache is not invalidated (inferred). | MB:748-751, 1335, 1446-1447; `useBrain.ts:82-96, 106-117` | GROUPING / SCALE | med | Offer cluster mode only with one deployment selected (or auto-select), and pass `activeDeploymentIds[0]`. |
| 12 | `groupBy` persists across sessions in localStorage; a returning user lands on grouped output they did not ask for. The sub-bar chip exists to explain it. | MB:304-306, 316, 1255-1261 | SIMPLICITY | low | Do not persist grouping (keep `thumbScale` and `imageView`). |
| 13 | Two controls open the same Advanced filters modal in one ribbon group. | MB:1284-1286, 1288-1299 | SIMPLICITY | med | Keep one (the button), drop the launcher. |
| 14 | Bulk bar renders "0 selected / All / None / Actions" permanently although its header comment says it appears once something is selected. | MBA:4-5, 96-139 | SIMPLICITY | med | Render the bar in the sub-bar only when `selectedIds.size > 0`; keep a single "Select all on page" affordance elsewhere. |
| 15 | "Issue" status filter can never match: `deriveAnnotationStatus` is only ever called without `hasError`. "Processing" filter is the grey fourth state DESIGN.md does not list. | MB:703-706, 1571-1574; `StatusBadge.tsx:16, 35-41, 118-131`; DESIGN.md:103-106 | SIMPLICITY | med | Drop "Issue" until BE-2; decide whether pending gets a badge at all (today every unprocessed card wears an identical grey badge) and align DESIGN.md. |
| 16 | Emoji carry meaning in labels: view toggle, size slider, all 9 group options, both tab icons, every Actions item, status and time filters, cluster group names, banners. DESIGN.md says icons are lucide-react. | MB:1230, 1235, 1272, 1305, 1310-1318, 1571-1574, 1636, 825-829, 245, 1384; MBA:156-217; DESIGN.md:69, 141 | SIMPLICITY | med | Plain text, or lucide icons where an icon earns its place. |
| 17 | Two different confidences on one AI card: the badge shows the lowest AI confidence, the label bar shows the verdict row's confidence. | MB:1023-1026, 1091-1093, 1120 | THUMBNAILS | med | One number, the verdict's. |
| 18 | Card shows scientific name only; the viewer shows vernacular first (`observationLabel`). File name occupies a line on every card at 10px. | MB:1112-1119; `lib/observations.ts:159-161` | THUMBNAILS | med | Use `observationLabel` on the card; drop the file name from the card (keep in viewer). |
| 19 | At scale 80 the image is 62px tall with three text lines at 11px/10px and a badge reading "⚙ 64%" covering much of the width; at 280 the grid is 4 columns. Nothing adapts to scale. | MB:938-939, 1110-1127; `StatusBadge.tsx:76-89` | THUMBNAILS | med | Below about 120: icon-only badge, no text lines; above about 200: allow a second label line. |
| 20 | Fixed pixel `height` under a stretching `minmax` column: real cards drift from 4:3 as the viewport changes, and uploading placeholders use `aspectRatio 4/3`, so rows mix heights. | MB:939, 1047-1055, 1000 | THUMBNAILS | low | `aspectRatio: '4 / 3'` on every card. |
| 21 | 100 `<img>` per page without `loading="lazy"`; Labels view renders one card per observation, so a page can be several hundred images. | MB:1057-1062, 1163-1170, 1199-1200 | SCALE / THUMBNAILS | med | `loading="lazy"`, `decoding="async"`; cap crop cards per photo. |
| 22 | A stuck deployment shows a Retry button on every card (100 identical buttons), each starting the same deployment-wide backfill. | MB:1063-1076 | THUMBNAILS | low | One deployment-level notice with one Retry. |
| 23 | Auto-retry fires one backfill POST per stuck deployment on the page; with many deployments stuck this is many jobs at once (bounded by the page's deployments, once per session). | `hooks/useAutoRetryThumbnails.ts:34-44` | SCALE | low | Cap concurrent auto-retries. |
| 24 | Pager is bottom-only with four buttons and no page-size choice; 15 rows of scrolling per page. "All" selects the page only but reads as everything. | MB:1245, 1493-1530 | SCALE / LABELLING | med | Prev/Next (and "1–100 of 1000") in the sticky ribbon; label "All on page". |
| 25 | Labelling 50 photos: circle-click first, Shift-click last, Actions, Label as, type, click a suggestion = 5 clicks plus typing, contiguous only. `SpeciesPicker` has no keyboard selection (no Enter/arrow handling), so the mouse is required at the end. No per-group "select all", so a species group still needs Shift-click across it. No Shift+Space range, no arrow-key movement in the grid, Ctrl+A only after a selection exists. | MB:727-729; `lib/cardSelection.ts:33-38`; `SpeciesPicker.tsx:150-200`; `MediaGroup.tsx` (no action slot default) | LABELLING | med | Per-group select-all; Enter picks the first suggestion; arrow keys and Shift+Space on cards. |
| 26 | `ClusterLabelAll` takes a free-text scientific name, not `SpeciesPicker`, so typos become labels with no `taxon_id`. | MB:73-80, 53-55 | LABELLING | med | Use `SpeciesPicker`. |
| 27 | Bulk label has no undo; delete does. | MB:602-611 vs 428-437 | LABELLING | low | Undo toast that deletes the inserted rows. |
| 28 | Six ad-hoc banner styles between ribbon and grid, plus a per-render `<style>` tag for the spinner. | MB:224-253, 251, 1349-1410 | SIMPLICITY | low | One Banner component with a tone prop. |
| 29 | Deployment options, 100 deployments: "location · 9 Jun 2026 · project" (project only when several projects), collisions get an id suffix, sorted by location name then newest, searchable, 280px-tall menu (about 9 rows), 320px max width. Fine for search, but 20 projects make every label long and truncated, and the alphabetical sort hides "newest". | DFO:58-76; `MultiSelect.tsx:98, 109, 121-140` | SCALE | low | Group the menu by project when multi-project; consider newest-first. |

## 3. Keep

1. The gesture model: plain click opens, circle or Ctrl toggles, Shift ranges across groups, Esc clears; unit-tested (`lib/cardSelection.ts`, MB:943-967, `gridOrder` flattens groups).
2. `photoVerdict` as the single verdict rule shared with the public API (`lib/observations.ts:100-119`); the grid already uses it for the label (MB:1020).
3. Deployment option labelling with date and project disambiguation plus type-to-filter (DFO:54-87, `MultiSelect.tsx:121-134`).
4. Sticky ribbon (MB:1209) and portalled dropdowns so menus clear the grid.
5. Thumbnail self-healing: processing banner, 30 s refresh while busy, one automatic backfill per deployment per session, Retry after a 10 min grace (`thumbnailRetry.ts`, `useRefreshWhileBusy.ts`, `useAutoRetryThumbnails.ts`).
6. Viewer flow: C/B/A/arrow keys with neighbour preview prefetch (`MediaDetail.tsx:240`, `usePrefetchNeighbours.ts`), and the Undo toast on delete (MB:428-437).

## 4. Open questions

- Does the API restrict Run AI, Train and batch delete by role (member vs project_admin)? Not visible from the frontend; decides how much of #1 is "hide" vs "disable".
- Is there a project-level role RPC cheap enough for the grid, or must #1 call `has_project_role` per deployment in view?
- Does `deployments` change identity after `useFilledTimezones` fills zones (`hooks/useFilledTimezones.ts:21`), resetting the page mid-review via MB:470? Inferred possible, not verified.
- Real data shape: species per deployment and detections per photo, which sets whether a server-side species filter needs a view in ww-backend.
- URL length limits for `in.(...)` with hundreds of deployment ids through Cloudflare/Supabase (#8).
- Ribbon height at 1280 is inferred (about 130px); not measured in a browser.
- Whether anyone uses the sex, life stage, annotator, model and annotation-type groupings; if not, the Group select can shrink to None, Species, Deployment, Cluster.
