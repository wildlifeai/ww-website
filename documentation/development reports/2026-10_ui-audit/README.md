# UI audit: simpler pages that fit the member's role and their data

> **Status:** 📋 Proposal, decisions taken 21 October 2026; the improvement agents are not scheduled yet. Five read-only audits of `frontend/src` at dev 77dae73, one per page group, plus a pass through the demo session on dev.

#### Author: Claude, with Victor Anton
#### 20 October 2026

## Outcome

The website shows every control to every role, reads whole tables into the browser and filters there, and carries three generations of page chrome plus several dead pages. 133 findings, 28 of them high, each with a file and line in the five slice reports:

| Slice | Pages | Findings (high) | Report |
|---|---|---|---|
| Shell and frame | header, project selector, Home, Toolkit, Manifest, Notifications, Processing history, Upload logs, dock, shared CSS | 36 (5) | [01-shell-nav.md](01-shell-nav.md) |
| Review grid | `/annotations`, `MediaBrowser` | 29 (4) | [02-grid.md](02-grid.md) |
| Labelling and review sub-pages | viewer, bulk label, review queue, cluster review, UMAP, event review, dataset health | 24 (8) | [03-labelling-review.md](03-labelling-review.md) |
| Insights, Reporting, Realtime | `/insights` tabs, `/reporting/:id`, `/field`, map, deployment table, dialogs, legacy My Data | 22 (6) | [04-insights-reporting-realtime.md](04-insights-reporting-realtime.md) |
| Settings, Upload, Admin, auth | `/settings` and its panels, `/upload-data` and triage, `/admin/*`, login, public pages | 22 (5) | [05-settings-upload-admin.md](05-settings-upload-admin.md) |

Three patterns account for most of the high findings.

**1. The UI does not read the member's role.** The frontend checks a role in four places (`useIsAdmin`, `DemoGuard`, `SettingsPage`, `MediaDetail` Remove). Everywhere else a `project_viewer` gets the same buttons as an admin, and the refusal arrives late or not at all: the viewer's Confirm and Relabel update zero rows and still show "Saved ✓" (03 #1); Edit location, Move, Delete, Defaults, Train model and New Project open their dialogs first (04 #4, 05 #2, #3, #5). The backend's own access check passes any active role, viewers included, for uploads, deployment creation, cluster confirm and queue review (`backend/app/authz.py` `_has_access`; 03 #3, 05 open question 1). Verified on dev: the demo account, a viewer, is offered New Project, Members, Defaults, Notifications and Change password on Settings.

**2. Scale is handled in the browser.** The grid's species, annotator and status filters apply to the 100 loaded rows, so a filter can show 3 of 2,000 and the pager still says "of 1000" (02 #4, #5). Insights pulls every observation in 1,000-row pages and hands three copies to Vega (04 #8); the deployment chart draws one bar per deployment with no cap (04 #6). Six reads have no paging and stop at 1,000 rows without saying so (04 #9, 05 #7, 01 E1, E4). Dataset Health and the legacy page fire one request per deployment (03 #19). The project selector has no search and shows eight rows (01 B1); the upload triage needs one decision per unmatched session, 30 cards for 30 sessions, each with a 100-option select (05 #6). Group by deployment on a 100-deployment selection gives 100 groups of one to three photos (02 #9).

**3. Chrome and dead weight.** Three tab-bar and three segmented-control implementations (04 #15); emoji as icons on almost every page against DESIGN.md's lucide rule; explanatory prose on Settings, Upload, Realtime, Manifest and Admin; the Advanced filters modal has two openers; the bulk bar is always on screen. Shipped but dead or fake: `/my-data-legacy`, `EventReviewPage`, `CamtrapImport.tsx`, `ControlBar`, `/upload/logs`, three placeholder export cards that download a two-line text file, a PNG button that always fails, and a dashboard editor that forgets on reload (04 #1 to #3, #22; 03 #22; 05 #13; 01 E2, F1).

What already works and must be kept is listed at the end of each slice report: the selection rule (#214), the grid's selection gestures and `photoVerdict`, the sticky ribbon and portalled menus, the slide-over in Settings, "Where these photos will go", `DataTable`, the move and delete dialogs with undo, and the thumbnail self-healing.

## Decisions

Taken with Victor on 21 October 2026: the recommendations below, with his changes to 7 and 8.

| # | Decision | Decided | Unblocks |
|---|---|---|---|
| 1 | Is `project_viewer` read-only everywhere, including uploads and the review APIs? | Yes. Add a write-level check to the website backend (`authz.py`: project_member or above for uploads, deployment creation, pipeline runs, cluster confirm, queue review) and hide every write control for viewers in the UI. The demo then follows the viewer rule and most `DemoDisabled` wrappers go. | 01 A4, D1; 02 #1, #2; 03 #1 to #3; 04 #4; 05 #2, #3, #22 |
| 2 | When a person corrects a label, edit the AI row (viewer today) or add a human row (bulk, cluster, queue today)? | Add a human row; AI rows are never overwritten. One rule for every surface, and the QA agreement number becomes measurable. | 03 #5, #6, #7, #23 |
| 3 | Delete now: `/my-data-legacy`, `EventReviewPage`, `CamtrapImport.tsx`, `ControlBar`, `/upload/logs`, the three placeholder export cards, the PNG button? | Yes, all seven. The CamtrapDP "still needs annotating" import mode goes with `CamtrapImport` unless wanted (05 open question 5). | 04 #1, #2, #22; 03 #22; 05 #13; 01 E2, F1 |
| 4 | Dashboard editor on Insights: persist it or remove it? | Remove it and ship the seven defaults with caps. | 04 #3, #6, #7 |
| 5 | Fold `/reporting/:id` into Insights Reports (`?deployment=`) and move exports to project level? | Yes. A per-day chart kind on Insights replaces the page; exports go to Toolkit, which already has the CamtrapDP card. | 04 #18, section 3 |
| 6 | Review Queue, UMAP and Dataset Health: keep as pages? | Queue: retire, its score becomes a "needs review first" sort in the grid. UMAP and Health: hidden unless their flag is on, reached from the grid only. | 03 #16 to #21, section 4 |
| 7 | Realtime tab: live now (the code inserts it, the comment says held back)? | Live, but hidden unless one of the user's projects has a device that sends or has sent LoRaWAN data (at least one `lorawan_messages` row for its deployments). A user with no such device never sees it. | 01 A2; 04 #9, #10 |
| 8 | Project Defaults for members: read-only or hidden? | Read-only for members, editable for project admins, so a member can see the settings and ask an admin to change them. | 05 #3, #4 |

Also settled by the code: project creation is allowed to `organisation_member` and above (ww-backend `50_projects.sql`, the INSERT policy), so the New Project button is for organisation roles, not project members (04 #5).

## Rules every improving agent follows

- **One role hook.** `useProjectRoles()` (from `user_roles` through the existing `has_project_role` / `adminScope` reads) answers `canWrite(projectId)`, `isProjectAdmin(projectId)`, `isOrgManager(orgId)` and `isSystemAdmin`. Pages hide what the role cannot do; nothing is shown and then refused. Every Supabase update carries `.select('id')` and treats zero rows as a refusal (the pattern of `deleteObservation`).
- **No list without a limit.** Above about ten items a picker gets a search box; above one page a list is paged server-side; filters run in the query, never on the loaded page; counts come from one scope; no request per row. Deployment pickers reuse `useDeploymentFilterOptions` with a searchable `MultiSelect`, grouped by project when several are selected.
- **One chrome.** One tab primitive, one segmented control, one banner slot, lucide icons only, no emoji, `--text-muted` instead of opacity, no prose on the page beyond one line with a guide link, one path to each action, nothing persisted in localStorage except thumbnail size and view.
- **Keep lists are binding.** Each slice's "Keep" section names behaviour the agent must not break; the e2e smoke and the vitest suites cover part of it.
- **Validation:** `npm run lint`, `npx tsc -b --noEmit`, `npx vitest run`, `npm run build`, `npm run size`, React Doctor clean, and a before and after screenshot of the page on dev at 1280x800 in the PR.
- **Database needs go to Victor first.** When a fix needs anything in Supabase (a column, a view, an RPC, an RLS or GRANT change), the agent stops and describes the need. Victor decides whether it becomes a ww-backend issue; agents never file one themselves.
- **Repo rules:** no em dashes, no database changes from here, backend layering, one PR per agent into `dev`.

## Agent plan

Agent 1 lands first; the rest run in parallel on its branch, then 7 to 9 last because they delete and move pages.

| # | Agent | Owns | Files | Needs |
|---|---|---|---|---|
| 1 | Role foundation | `useProjectRoles`, backend write check, `.select('id')` on updates, demo through the viewer rule | `hooks/`, `lib/observationWrites.ts`, `backend/app/authz.py`, `components/common/DemoGuard.tsx` | decision 1 |
| 2 | Review grid | 02 all | `components/data/MediaBrowser.tsx`, `MediaGroup.tsx`, `MediaBulkActions.tsx`, `ui/MultiSelect.tsx`, `ui/StatusBadge.tsx`, `lib/deploymentFilterOptions.ts`; a server-side filter may need a database view, raised to Victor first | 1, decision 6 |
| 3 | Viewer and labelling | 03 #1, #2, #4, #5, #7 to #15, #23, #24 | `components/data/MediaDetail.tsx`, `MediaDetailImage.tsx`, `SpeciesPicker.tsx`, `BulkLabelModal.tsx`, `common/UndoToast.tsx` | 1, decision 2 |
| 4 | Insights and map | 04 #3, #6 to #9, #11 to #17, #20 | `pages/InsightsPage.tsx`, `components/data/ReportsDashboard.tsx`, `chartSpec.ts`, `DeploymentMap.tsx`, `DeploymentBulkActions.tsx`, `ui/DataTable.tsx`; server-side aggregation may need a database view, raised to Victor first | 1, decision 4 |
| 5 | Settings | 05 #1, #3 to #5, #8, #9, #12, #14 to #16, #22 | `pages/SettingsPage.tsx`, `components/settings/*`, `components/data/ProjectMembersPanel.tsx`, `CreateProjectModal.tsx` | 1, decision 8 |
| 6 | Upload and jobs | 05 #6, #7, #10; 01 E1, E3 to E5 | `components/upload/*`, `pages/ProcessingHistoryPage.tsx`, `NotificationsPage.tsx` | 1 |
| 7 | Shell, Home, Toolkit | 01 A, B, C, D, F | `App.tsx`, `common/GlobalProjectSelector.tsx`, `NoProjectSelected.tsx`, `pages/HomePage.tsx`, `ToolkitPage.tsx`, `components/toolkit/GenerateManifest.tsx`, `styles/index.css`, `ui/Ribbon.tsx` | 1, decision 7 |
| 8 | Review sub-pages | 03 #16 to #22, section 4 | `pages/ReviewQueuePage.tsx`, `ClusterReviewPage.tsx`, `UmapExplorerPage.tsx`, `DatasetHealthPage.tsx`, `EventReviewPage.tsx` | 2, decision 6 |
| 9 | Reporting, Realtime, deletions | 04 #1, #2, #5, #10, #18, #19, #21, #22; 05 #13; 01 E2, F1; 05 #17 to #21 | `pages/ReportingPage.tsx`, `FieldPage.tsx`, `MyDataPage.tsx`, `components/data/CamtrapImport.tsx`, `ui/ControlBar.tsx`, `pages/UploadLogsPage.tsx`, `LoginPage.tsx`, `AdminUsagePage.tsx` | 4, decisions 3, 5, 7 |

## Seen on dev through the demo session

The demo project has one deployment and no photos, so dev shows layouts, not scale. Three things it confirmed: the Settings row offers the viewer every admin action; the Review toolbar carries eleven controls on an empty grid; the project selector is missing from the header on Processing history. Screenshots at scale need a signed-in account on staging (188 deployments) and were not taken.

## Not verified

Widths, row heights and above-the-fold counts are read from the JSX, not measured. Viewer behaviour at the API (03 #3, 05 open question 1) is read from `authz.py`, not run. Each slice lists its own open questions; the data-shape ones (projects per user, deployments per project, observations per project on staging) size several of the scale fixes and are a read-only query away.

**21 October 2026, agent 1.** The write check now covers the routes in decision 1 ([API reference, Access](../../resources/api-reference.md#access)). Two things the slices did not say: `POST /api/inat/publish` read any photo by id with the service role, with no project check at all (now write-checked); and the batch deletes, the location edit and project delete run as the caller, so RLS already refused a viewer there. `DemoGuard.tsx` was left to the page agents.
