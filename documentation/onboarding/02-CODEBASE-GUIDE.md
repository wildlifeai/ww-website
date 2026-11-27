# 02 — Codebase Guide

How the repository is organised, how the frontend is routed, and how the backend is layered.

## Repository layout

```
ww-website/
├── readme.md                  # entry point (setup + command reference)
├── docker-compose.yml         # full-stack run (lean API image)
├── docker-compose.dev.yml     # dev overlay: ML image + Drive credential mount — always use BOTH files
├── documentation/             # all docs (onboarding / resources / development reports)
├── scripts/                   # dev utilities (seed-fixture generator, demo seeding, exports, …)
├── test-fixtures/             # camera-trap SD-card fixtures for upload testing (see its README)
├── e2e/                       # Playwright browser flows, run in CI (see its README)
├── infra/aws/                 # SAM templates for the AWS stacks (shared.yaml, template.yaml)
├── cluster/reannz/            # the REANNZ cluster's cron, GPU job and image pull scripts
├── .agents/                   # agent rules: SKILL.md, its reference files, DESIGN.md
├── backend/                   # FastAPI service
└── frontend/                  # React + Vite SPA
```

## Frontend (`frontend/src/`)

```
src/
├── App.tsx                 # Router, signed-in Layout (Toolkit, Realtime, Media, Insights nav), auth guard, UploadProvider
├── main.tsx                # React entry
├── pages/                  # route-level components
├── components/
│   ├── ui/                 # design-system primitives (Modal, Ribbon, DataTable,
│   │                       #   FilterSelect, StatusBadge, VegaChart) — see resources/ui-components.md
│   ├── data/               # feature components (MediaBrowser, MediaDetail, SpeciesPicker,
│   │                       #   DeploymentMap, ReportsDashboard, …)
│   ├── upload/             # UploadFlow (+ UnassignedTriage, DeploymentPicker, upload.css), ProgressDock
│   ├── toolkit/            # GenerateManifest, UploadModel, PipelineStatusBox
│   └── common/             # nav/shared bits
├── contexts/UploadContext.tsx  # global upload store (survives navigation)
├── hooks/                  # useAuth, useJob, useBrain, useIntelligence, useGridBulkActions, …
├── lib/                    # apiClient (fetch + Supabase JWT), observations (provenance helpers),
│                           #   projectMembers (membership + invitation RPCs)
├── config/supabase.ts      # Supabase client init
└── styles/index.css        # CSS variables + base styles
```

### Signed-in navigation

Defined as `USER_TABS` in `App.tsx`. The active tab is highlighted, so pages no longer repeat
their own title, and a deep route lights its parent tab (`lib/navTabs.ts`). Lifecycle order:
**prepare → collect → analyse**. The 📡 **Realtime** tab opens only when the projects selected in
the header have a camera the page shows, deployed now or collected today with a ping period over
0 or a `lorawan_messages` row (`hasLorawanCameras`, `hooks/useHasRealtimeData.ts`); otherwise it
shows greyed out, and hovering or clicking it says why and links the LoRaWAN guide (`components/common/RealtimeTabOff.tsx`). Below 900 px the tabs show
their icon only. Write controls (the header Upload button, Home's upload step, Toolkit's Prepare
SD card) hide for a user who can write to no project (`hooks/useWritableProjects.ts`).

| Tab | Route | Page |
|-----|-------|------|
| 🧰 Toolkit | `/toolkit` | `ToolkitPage` (motion live view, model upload, iNaturalist, CamtrapDP export, the GBF report behind `FF_GBF_REPORT_ENABLED`, SD-card prep; each card by role) |
| 📡 Realtime | `/field` | `FieldPage` (live-deployment view; shown only with LoRaWAN data) |
| 🏷️ Media | `/annotations` | `AnnotationsPage` → `MediaBrowser` (+ full-screen modal) |
| 📈 Insights | `/insights` | `InsightsPage` (projects, deployments, charts, maps) |

### Routes

**Public**: `/`, `/login`, `/signup`, `/reset-password`, `/privacy`, `/terms`, `/resources`, `/faq`, `/guides`,
`/guides/:slug` (lazy-loaded); `/support` redirects to `/faq`. The other public pages are in the main
chunk so they render without a "Loading…" first; every protected page is lazy-loaded.

`/login` and `/signup` are one `LoginPage`: email sign-in (Supabase Auth UI), an email sign-up form
that sends `given_name` and `family_name` (`lib/signUp.ts`), and Google sign-in on both. With
`VITE_GOOGLE_CLIENT_ID` set that is Google's own button (`lib/googleIdentity.ts`): the ID token
comes back to the page and goes to `signInWithIdToken`, so Google's consent screen names this site,
not `<ref>.supabase.co`; each site origin must be an Authorised JavaScript origin of that client.
Without it, the redirect flow. Redirects and confirmation links return to the site's own origin,
which must be on the Supabase project's redirect allow-list (#187). A new account lands in the General organisation via ww-backend's
`handle_new_user`; a Google-only account has no password until it sets one in Settings (Change
password) or uses Forgot password.

**Protected (`RequireAuth`)**

| Route | Page | Purpose |
|-------|------|---------|
| `/toolkit` | `ToolkitPage` | Motion live view, privileged model upload, iNaturalist, CamtrapDP export, GBF contribution report, SD-card prep |
| `/annotations` | `AnnotationsPage` | Filter/browse media; click a photo → full-screen labeling modal |
| `/insights` | `InsightsPage` | Projects/Deployments, charts, maps |
| `/field` | `FieldPage` | Realtime: LoRaWAN cameras deployed now, and those that ended today, read-only. A deployment shows when its ping period is over 0 or it has sent a message (`isLorawanCamera`); one with no period recorded and no message is left out. With no GPS position among them, a line says there is no map. Cameras tab: each one's newest LoRaWAN check-in, battery, SD free, temperature and health (`health_state`, `health_reasons`) from the `deployment_latest_lorawan` view, its ping period and firmware versions from the deployment, and its own detection count (`lib/realtimeReads.ts`). The state (Finished, LoRaWAN off, Silent after 3 ping periods, Awaiting, Fault, Warning, Stale, OK, or Checking in for a camera that sends no health report) and the reasons in words are `lib/cameraState.ts` only (#427). Messages tab: their LoRaWAN messages, live, check-ins, detections and the answers to settings sent by downlink (`lib/lorawanFeed.ts`, `hooks/useFieldLive.ts`). Nothing navigates away |
| `/upload-data` | `UploadDataPage` (→ `UploadFlow`) | The single upload surface (the header **Upload** button, Home and the guide link here; demo users get `RequireNotDemo`) |
| `/manifest` | `ManifestPage` | Firmware `MANIFEST.zip` builder |
| `/live-view` | `LiveViewPage` | A bench tool for a WW500 on its USB cable through Web Serial (Chrome or Edge on a computer), no backend: the live picture with the motion grid and wake status, and Controls (run modes, Save frame, frame interval, motion ops, Keep awake), Console (commands, session log) and Device tabs. The console protocol is `lib/liveView.ts` and `lib/liveViewSession.ts`, ported from the firmware's `live_view.py` and `live_preview.md` (#400) |
| `/upload-model` | `UploadModelPage` | Edge Impulse → Vela model upload (org managers) |
| `/clusters/:id` | `ClusterReviewPage` | Bulk-confirm HDBSCAN clusters, unconfirmed and biggest first, ten per page; a confirm offers Undo; a cluster that mixes species is marked and its confirm warns first (#450) |
| `/review/:id` | `ReviewQueuePage` | Retired Review Queue: redirects to `/annotations?deployment=:id` |
| `/umap/:id` | `UmapExplorerPage` | Embedding-space map of a deployment; a click opens that photo in the grid |
| `/intelligence/:id` | `DatasetHealthPage` | Dataset health + QA agreement (per project), behind `FF_INTELLIGENCE_ENABLED`. Links to a flagged page show only when `useFeatureEnabled` says the feature answers |
| `/processing` | `ProcessingHistoryPage` | Upload & pipeline job history, filtered by status in the read, 50 at a time ("Load more"), without the sign-in iNaturalist sync; `?job=<id>` opens that job's row |
| `/notifications` | `NotificationsPage` | Notification inbox, read **direct from Supabase** via `useNotifications` (there is no `/api/notifications` router), filtered in the query and paged with "Load more"; the unread count (a server-side count) drives the nav badge. Delivery/email config (`EMAIL_PROVIDER`, `RESEND_API_KEY`, …): [deployment-guide](../resources/deployment-guide.md#full-pipeline-config-checklist-per-subsystem) |
| `/settings` | `SettingsPage` | Projects (a row opens its settings in a slide-over), the account (change password) and integrations (iNaturalist, organisation API keys) |
| `/admin/usage` | `AdminUsagePage` | Per-user usage limits (platform admin) |
| `/admin/devices` | `AdminDevicesPage` | Every device with its latest deployment and that project's organisation; the platform admin sets or corrects a camera's LoRaWAN DevEUI there (`lib/deviceEui.ts`, #525) |

**Redirects / legacy**: `/results` → `/insights`, `/other` → `/toolkit`, `/my-data` → `/insights`,
`/analyse-images` → `/upload-data` (query strings preserved), `/explore/:id` →
`/annotations?deployment=:id` (the Annotations grid now does clustering, bulk cluster-confirm and
similar-image search), `/analysis/:id` → `/reporting/:id` → `/insights?tab=reports&deployment=:id`
(one deployment's results are the Insights Reports tab filtered to it; the CamtrapDP export is on
Toolkit), `/my-data-legacy` → `/insights`, `/upload/logs` → `/processing` (job logs live in
Processing history). `App.tsx` redirects these; the old `ReportingPage`, `MyDataPage` and
`UploadLogsPage` were deleted ([UI audit](../development%20reports/2026-10_ui-audit/README.md),
decisions 3 and 5). `LabelingPage` was **removed**: its rich editor now lives in the Annotations
full-screen modal (see [05-ANNOTATION-WORKFLOW](./05-ANNOTATION-WORKFLOW.md)). `EventReviewPage` was
**deleted** (UI audit decision 3).

**Fallback**: any unmatched path renders `FourZeroFourPage` (404) — the wildcard `*` route at the
end of `App.tsx`. Client-side only: the Cloudflare Pages SPA fallback still serves HTTP 200.

## Backend (`backend/app/`)

Layered so HTTP concerns never leak into business logic:

```
routers/   → thin HTTP controllers: validate input, delegate, shape ApiResponse
domain/    → pure business logic, NO FastAPI/HTTP imports (unit-testable)
services/  → infrastructure adapters (Supabase, Azure, S3, Drive, Lambda, SSM, Vela, SpeciesNet, http)
schemas/   → Pydantic request/response models
jobs/      → async job system (definitions, dispatch, runner, store, ARQ worker, the REANNZ batch's ingest)
middleware/→ request id, structured logging, rate limiting, CORS
registries/→ static config (camera configs, model + embedding registries)
```

**Routers** (`/api/*`, registered in `app/main.py`): `auth` (demo session), `admin` (system-admin
device list), `jobs`, `deployments`, `projects`, `exif`, `manifest`, `exports`, `reports` (the GBF
report, answering `FEATURE_DISABLED` without `FF_GBF_REPORT_ENABLED`), `models`, `media`,
`public_api`, `inaturalist`, `uploads` (presigned S3 uploads), `worker` (the REANNZ batch's lease
routes, 404 until `WORKER_TOKEN_SHA256` is set), `lambda_events` (`/events`, only under
`WW_LAMBDA_ROLE`), and, each behind its feature flag, `camtrapdp`,
`pipeline`, `brain`
(embeddings/clusters/UMAP/similarity), `qa` (AI-vs-human agreement), `intelligence`
(dataset health/alerts).

**Key domain modules**: `exif`, `photo_preprocessing`, `pipeline` (SpeciesNet steps), `events`,
`wildlife_brain` + `embedding_lifecycle` + `active_learning` (the DINOv3 "Brain"),
`human_verdicts` (cluster confirm and undo, batch review: one human row per photo),
`intelligence`, `media_registry` / `media_resolver`, `model`, `manifest`, `camtrapdp`, `camtrapdp_export`, `inaturalist`,
`public_api`, `gbf_report` + `gbif_dataset` (the GBF report and a project's GBIF dataset),
`batch_lease` (the REANNZ batch's leases), `zip_limits` (ZIP input limits).

**The AWS path** (behind settings whose defaults keep Azure,
[settings](../resources/deployment-guide.md#aws-and-the-reannz-batch)): `app/aws_params.py` (SSM
parameters into the environment) and `app/lambda_events.py`; `services/object_store.py` (Azure Blob,
S3 or a local directory, and the archive), `services/incoming.py` (presigned uploads),
`services/lambda_invoke.py` (jobs to the `cpu` function); `jobs/batch_infer.py` (the cluster's
inference) and `jobs/ingest.py` (its results into the pipeline). Other jobs with a file of their own:
`jobs/pipeline_run.py` (*Run AI again*), `jobs/camtrapdp_import.py`, `jobs/inat_sync.py`.

### Adding a feature (backend)

1. **Schema** — Pydantic models in `schemas/`
2. **Domain** — logic in `domain/` (no HTTP imports)
3. **Router** — thin endpoint in `routers/` (validate → delegate)
4. **Register** — include the router in `app/main.py`
5. **Test** — add to `tests/`

See [03-DATA-AND-SYNC](./03-DATA-AND-SYNC.md) for the job system and the Supabase RLS/GRANT model,
and [04-AI-PIPELINE](./04-AI-PIPELINE.md) for the inference layer.
