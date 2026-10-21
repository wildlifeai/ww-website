# UI audit, slice: app shell and frame pages

Worktree research/ui-audit (origin/dev 77dae73), read-only. Paths under `frontend/src/`. Widths are estimated from the JSX, not measured; anything so derived is marked *inferred*.

## A. App shell (App.tsx, banners, AccountMenu)

**What it is.** `Layout` (App.tsx:312-487) renders a sticky 56 px header: logo (370), `GlobalProjectSelector` (384), nav tabs Toolkit / [Realtime] / Review / Insights (391-399), spacer, Upload button (415, hidden for demo) and the avatar `AccountMenu` (416). Below it: demo banner (432-449), then `<main>` with `UploadQuotaBanner` (453), `InvitationsBanner` (454) and the page. Footer carries Get started / Guides / FAQ / Privacy / Terms (462-484). 7 to 8 header controls for every signed-in role. `AccountMenu` (97-264) lists identity, up to 5 recent notifications (174), Notifications, Processing history, Settings, User usage and Devices (ww_admin only, 218-239), Upload Model (org manager only, 240-250), Logout.

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| A1 | Header pieces are all `flexShrink: 0` + `nowrap` and index.css has no header breakpoint; *inferred* width ~915 px with 3 tabs, ~1,025 px with Realtime, so the header overflows below ~1,000 px and at tablet width | App.tsx:376,385,392,350; index.css:160,225 (only breakpoints) | SIMPLICITY | high | One breakpoint (~900 px): collapse tab labels to icons, drop the logo text, let the selector shrink |
| A2 | Realtime tab is driven by a count of open-ended deployments across every project RLS lets the user read, not the selection, seeded from localStorage; the comment says it is "held back" but the code inserts it | App.tsx:305-307 vs 332-336; useHasActiveDeployments.ts:22-31 | ROLE/CONSISTENCY | med | Decide: ship it (fix comment, key on selected projects) or remove it |
| A3 | Active tab uses `startsWith`, so /clusters, /review, /reporting, /intelligence, /manifest, /upload-model, /upload-data, /settings, /notifications, /processing, /upload/logs light no tab | App.tsx:339 | SIMPLICITY | med | Map each deep route to its parent tab (Review or Insights) |
| A4 | Upload button, Home step 1 and "Upload anyway" show for project_viewer; routers gate only `require_not_demo`, no project-role check in the website layer | App.tsx:415; HomePage.tsx:134,173; media.py:186-253 | ROLE | med | Resolve open question 1, then hide upload entry points for viewer |
| A5 | Three banner types can stack above the page (Prototype on Home, quota, invitations, plus the demo bar), each a different colour and icon | HomePage.tsx:121; App.tsx:432-454; PrototypeBanner.tsx | SIMPLICITY | med | One banner slot, one style, newest first; move the prototype notice to the footer or a dismissable one-liner |
| A6 | Menu items use emoji glyphs (🔔 🗂 ⚙ 📊 📡 🤖 ←) while the nav uses lucide | App.tsx:198-258 vs 294-300 | CONSISTENCY | low | lucide icons at 15 px, as `tabLabel` does |
| A7 | `managed-orgs` is fetched in Layout and Toolkit (same key, deduped) and again in UploadModel with a different key | App.tsx:316-327; ToolkitPage.tsx:66-75; UploadModel.tsx:38-46 | CONSISTENCY | low | One `useIsOrgManager` hook beside `useIsAdmin` |
| A8 | Notifications poll every 45 s on every signed-in page just to badge the avatar; jobs poll every 15 s while Processing history is open | useNotifications.ts:73; useJobs.ts:24 | SCALE | low | Keep, but note it before adding more shell polling |
| A9 | Guides and FAQ are reachable for a signed-in user only from the footer | App.tsx:473-477 | SIMPLICITY | low | Add "Help" to AccountMenu |

## B. GlobalProjectSelector, NoProjectSelected, selection

**What it is.** A 150 px-label button (GlobalProjectSelector.tsx:75) opening a 250 px fixed portal menu (8, 85-96): Select All / Clear row, a 300 px scroll list of every project (112-141), "Manage projects…" → /settings (143-154). `selectionAfterLoad` ticks everything on first load for a user and keeps "all" as all on reload (projectSelection.ts:321-336), so "All Projects" is the default after login (confirmed). The selector renders nothing when the user has 0 projects (54).

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| B1 | No search; 300 px list is ~8 rows (*inferred* ~37 px/row), so 20 projects scroll 2.5 screens and 50 six; names ellipsised with no `title` | GlobalProjectSelector.tsx:112,135 | SCALE | high | Add a filter input above 10 projects, `title` on rows, widen to 320 px, group by organisation when >1 org |
| B2 | 0-project user: selector hidden, every page shows "Nothing to display, you have no project selected" with no button, a dead end; only Home has a real empty state | GlobalProjectSelector.tsx:54; NoProjectSelected.tsx:168-169; HomePage.tsx:156-178 | ROLE/SIMPLICITY | high | NoProjectSelected: when `projects.length === 0` say "You have no projects yet" and link /settings?create=true |
| B3 | Tailwind class strings (`hover:bg-gray-50`, `focus:ring-blue-500`) with no Tailwind in the repo, so rows have no hover state | GlobalProjectSelector.tsx:60,128; DESIGN.md:67 | CONSISTENCY | low | Remove; add an inline hover tint |
| B4 | "N Projects" label never names them; nothing else in the shell shows the active scope | projectSelection.ts:379 | SIMPLICITY | low | Tooltip listing the ticked names |

## C. HomePage (signed-in dashboard)

**What it is.** Prototype banner, "Welcome back, {email local-part} 👋", a 3-card ThreeStepGuide (Upload / Review / Insights), then "Your Projects": one card per project (📂, name, description, deployment count, "Open →" which sets the selection to that project and goes to /insights?tab=deployments, and a "📊" button to /intelligence/:id) (HomePage.tsx:85-182). Identical for every role. *Inferred* above the fold at 1280x800: banner, greeting, 3 guide cards, first row of 4 project cards = ~11 clickable things.

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| C1 | Lists every project regardless of the header selection, ordered by `created_at` desc while the selector orders by name; 20 projects become a 5-row card wall under a guide the user has read before | HomePage.tsx:98-103,142; useProjectSelection.tsx:444 | SCALE/CONSISTENCY | high | Show selected projects first, compact rows past ~8, same order as the selector; make the guide dismissable |
| C2 | Three project lists in the product: Home cards, Settings table (same `onRowClick` to /insights?tab=deployments) and the Insights deployments tab (*inferred*) | HomePage.tsx:147; SettingsPage.tsx:191,280 | CONSISTENCY | med | One canonical list (Insights or Settings); Home links to it |
| C3 | Query embeds `deployments(id)` for every project only to count: 20 × 100 deployments = 2,000 rows per Home visit (*inferred*) | HomePage.tsx:100,109 | SCALE | med | Count via a view/RPC or `select('deployments(count)')` |
| C4 | Dataset Health (/intelligence) is reachable only behind an emoji button with a `title` | HomePage.tsx:73-75,148; App.tsx:555 | SIMPLICITY | med | Name it in text or move it into Insights |
| C5 | Guide copy is duplicated for the marketing variant and ends in arrows and emoji; step 1 is Upload for viewer and demo | guideSteps.ts:106-158; DemoGuard.tsx:191 | ROLE/SIMPLICITY | low | Share the descriptions; swap step 1 to "Browse" when the user cannot upload |
| C6 | Greeting uses the email local-part ("Welcome back, john.smith") | HomePage.tsx:116 | SIMPLICITY | low | Use the profile name or drop the greeting |

## D. Toolkit, Manifest, Upload model

**What it is.** ToolkitPage.tsx:79-162: heading, intro line, four groups, five 720 px cards: Prepare SD card (→ /manifest), AI models (org manager: link; everyone else a greyed "requires Organisation Manager" card, 111-115), Connect iNaturalist (inline panel), Export dataset for R (enabled only with exactly one project selected, 77, with an explanatory sentence, 135-139), Publish to GBIF (hardcoded coming soon, 157). Only the AI-models card is role-aware. GenerateManifest (/manifest) is a 3-step form: firmware select of every Himax firmware, project select of every active project (its own query, 50-63), model info with OP 14/15 ids (287-288), a 4-item checkbox checklist (437-442). UploadModel is org-manager only (486 lines, three sources, label mapper).

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| D1 | A viewer or a member who never configures cameras sees Prepare SD card, a greyed AI-models card, iNaturalist, Export and a coming-soon card; nothing is hidden by role or by data | ToolkitPage.tsx:87-159; manifest.py:27 (`get_optional_user`) | ROLE/SIMPLICITY | high | Hide the AI-models placeholder and GBIF for non-managers; show Prepare SD card only to project_admin/org roles |
| D2 | Manifest project select ignores the header selection and lists all projects with no search; the page has no way back to Toolkit | GenerateManifest.tsx:50-63,247-258; ManifestPage.tsx | SCALE/CONSISTENCY | med | Default to the single selected project; breadcrumb to /toolkit |
| D3 | Two paragraphs of background, firmware internals (OP 14/15, variant notes) and an unsaved checklist on the form | GenerateManifest.tsx:164-169,234-238,284-289,436-442 | SIMPLICITY | med | Move explanation to a guide; keep model name + version only |
| D4 | iNaturalist panel hides itself on 401/404 but the surrounding card and description still render empty | INaturalistPanel.tsx:87-90; ToolkitPage.tsx:120-126 | SIMPLICITY | low | Let the panel own its card |
| D5 | Emoji card icons (💾 🤖 🕊 📦 🌍) beside a lucide heading; button labels carry emoji | ToolkitPage.tsx:81,89-154,149 | CONSISTENCY | low | lucide icons, plain labels |
| D6 | Demo can press Export and Generate; no `DemoDisabled`; exports.py gates only `get_current_user` | ToolkitPage.tsx:141-150; exports.py:34 | ROLE | low | Wrap with DemoDisabled if the API refuses, otherwise fine |

## E. Notifications, Processing history, Upload logs, ProgressDock

**What it is.** Notifications: latest 100 rows (useNotifications.ts:64,80), client-side type select + unread checkbox + "N shown" + Mark all read. Processing history: latest 50 jobs (useJobs.ts:11; API cap 200, jobs.py:23), expandable rows that fetch the log on open (ProcessingHistoryPage.tsx:371). Upload logs: live `UploadContext` state only (in-memory, UploadContext.tsx:132), breadcrumb "Annotations › Upload Logs", four summary tiles, the PipelineStatusBox, and Minimise / Dismiss & go to Annotations / View Annotations / Clear logs. ProgressDock mounts at App level (App.tsx:500) so it persists across routes, opens on `startUpload` (UploadContext.tsx:376), fixed bottom-right, 360 px wide, up to `100vh - 2.5rem` tall, z 300 (ProgressDock.tsx:190-197,267-270).

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| E1 | No paging on either list; the avatar badge counts unread only within the latest 100; jobs have no kind/status/project filter | useNotifications.ts:64,89; useJobs.ts:11; ProcessingHistoryPage.tsx:447 | SCALE | med | Cursor paging ("Load more"), server-side unread count, a status filter for jobs |
| E2 | Upload logs and Processing history are two pages for one thing; the logs page is empty after a reload, has a wrong breadcrumb and three overlapping end actions | UploadLogsPage.tsx:479-483,491-510,579-597 | CONSISTENCY/SIMPLICITY | med | Retire /upload/logs; the dock's "full logs" opens the job row in Processing history |
| E3 | Medium dock can cover a 360 px by near-full-height strip of content (map controls, last table rows, *inferred*) and cannot be moved; completion offers four exits (⤢, See Insights, Review & label, Dismiss) | ProgressDock.tsx:267-270,298-304,346-384 | SIMPLICITY | med | Default to the pill once the upload is processing; one primary CTA |
| E4 | Summary line reads every AI observation for the uploaded deployments with no limit; a large upload hits the 1,000-row cap and the species/detection counts are silently wrong | ProgressDock.tsx:65-77 | SCALE | med | Count server-side or drop the line |
| E5 | Emoji type/kind icons and 🎉 empty state; ℹ️/⬆️ vary by platform | NotificationsPage.tsx:193-198,273; ProcessingHistoryPage.tsx:339-343 | CONSISTENCY | low | lucide |

## F. Shared chrome: Ribbon, ControlBar, index.css, DemoGuard, ThreeStepGuide

| # | Finding | Evidence | Lens | Sev | Direction |
|---|---|---|---|---|---|
| F1 | `ControlBar` has no users; only `FilterSelect`, `SearchInput`, `ColumnToggle` are imported; DESIGN.md still names it the page pattern | grep: MediaBrowser.tsx:10, DataTable.tsx:2; DESIGN.md:136 | CONSISTENCY | med | Delete it or make it the chrome for Toolkit/Notifications/Processing; fix DESIGN.md |
| F2 | Ribbon (Insights, MediaBrowser only) adds a second brand chip 🌿 and a second tab row under the header; stats hidden below 1180 px is the only responsive rule; body scrolls horizontally | Ribbon.tsx:420-423,360; index.css:160-162 | SIMPLICITY | med | Drop the brand chip; wrap groups below ~900 px |
| F3 | `.glass-card` used in DemoGuard and four pages but defined nowhere; `--surface-2`, `--text` undefined with no fallback; `--warning` undefined with two different fallbacks | DemoGuard.tsx:80; INaturalistPanel.tsx:121,174,220; PipelineStatusBox.tsx:71 vs GenerateManifest.tsx:211 | CONSISTENCY | med | Define `--warning`, replace `glass-card` with `.card`, fix the two tokens |
| F4 | `--text-muted` exists for WCAG (#212) but shell pages still dim text with opacity (26 uses in the slice files) | index.css:7-10; ToolkitPage.tsx:82; NotificationsPage.tsx:237; ProcessingHistoryPage.tsx:436 | CONSISTENCY | low | Replace opacity-on-text with `var(--text-muted)` |
| F5 | Only three breakpoints in the app (1180 ribbon, 760 resources, 640 upload.css); no grid or header rule under 1024 | index.css:160,225; upload.css:147 | SIMPLICITY | med | Add shared `@media (max-width: 900px)` rules for header and card grids |
| F6 | Em dashes in UI copy (demo toast, prototype banner, "View all" (with an em dash)) | DemoGuard.tsx:191,275; PrototypeBanner.tsx:297,301; App.tsx:198 | CONSISTENCY | low | Commas or a new sentence (repo rule) |

## Keep

1. The selection rule (#214): select-all on load, empty means none, `queryProjectIds`/`noProjectSelected`, refetch on tab return and `reloadProjects()` after writes (projectSelection.ts, useProjectSelection.tsx:457-464).
2. The selector's body-portal, fixed placement and z 2000 so it clears Leaflet; rAF-throttled repositioning (GlobalProjectSelector.tsx:15-52).
3. The z-index ladder: header 100, ribbon 50 sticky at 56 px, slide-overs 200, modal/dock 300 (Ribbon.tsx:410-413; ProgressDock.tsx:194).
4. Lazy signed-in pages with public pages in the main chunk (App.tsx:32-59, #228); `RequireAuth` and `RequireNotDemo` as route wrappers.
5. Dock capped to the viewport with a pinned header and a Dismiss that works when stalled (ProgressDock.tsx:267-322).
6. Per-row lazy log fetch in Processing history (ProcessingHistoryPage.tsx:371); `useIsAdmin` returning null while resolving (useIsAdmin.ts:513-522).
7. `--text-muted`, the `a[target=_blank]` arrow rule and the `.btn-outline` variant (index.css:7-10,189-200,249-252).

## Open questions

1. Can a project_viewer insert media through the website (RLS on `media`/pipeline enqueue)? The routers gate only `require_not_demo`. Decides A4 and C5.
2. Does ww_admin read every deployment and project under RLS? If so Realtime is always on and Home lists every project in the system for admins.
3. Is the Realtime tab meant to be live now (App.tsx:305-307 says held back, 332-336 inserts it)?
4. Is Dataset Health (/intelligence/:project_id) a supported page or legacy? It is reachable only from the Home emoji button, Settings and the legacy MyData page.
5. Is /upload/logs still wanted now that Processing history archives job logs?
6. Real distributions: projects per user and notifications/jobs per user on production, to size B1 and E1.
