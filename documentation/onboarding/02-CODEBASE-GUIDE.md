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
├── backend/                   # FastAPI service
└── frontend/                  # React + Vite SPA
```

## Frontend (`frontend/src/`)

```
src/
├── App.tsx                 # Router, signed-in Layout (Toolkit·Review·Insights nav), auth guard, UploadProvider
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
├── hooks/                  # useAuth, useJob, useBrain, useIntelligence, usePipeline, …
├── lib/                    # apiClient (fetch + Supabase JWT), observations (provenance helpers),
│                           #   projectMembers (membership + invitation RPCs)
├── config/supabase.ts      # Supabase client init
└── styles/index.css        # CSS variables + base styles
```

### Signed-in navigation

Defined as `USER_TABS` in `App.tsx`. The active tab is highlighted, so pages no longer repeat
their own title, and a deep route lights its parent tab (`lib/navTabs.ts`). Lifecycle order:
**prepare → collect → analyse**. The 📡 **Realtime** tab appears only when one of the user's
projects has a device that sends or has sent LoRaWAN data, at least one `lorawan_messages` row the
user can read (`hooks/useHasRealtimeData.ts`, UI audit decision 7). Below 900 px the tabs show
their icon only. Write controls (the header Upload button, Home's upload step, Toolkit's Prepare
SD card) hide for a user who can write to no project (`hooks/useWritableProjects.ts`).

| Tab | Route | Page |
|-----|-------|------|
| 🧰 Toolkit | `/toolkit` | `ToolkitPage` (SD-card prep, model upload, iNaturalist, CamtrapDP export, the GBF report behind `FF_GBF_REPORT_ENABLED`; each card by role) |
| 📡 Realtime | `/field` | `FieldPage` (live-deployment view; shown only with LoRaWAN data) |
| 🏷️ Annotations | `/annotations` | `AnnotationsPage` → `MediaBrowser` (+ full-screen modal) |
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
| `/toolkit` | `ToolkitPage` | SD-card prep, privileged model upload, iNaturalist, CamtrapDP export, GBF contribution report |
| `/annotations` | `AnnotationsPage` | Filter/browse media; click a photo → full-screen labeling modal |
| `/insights` | `InsightsPage` | Projects/Deployments, charts, maps |
| `/field` | `FieldPage` | Realtime: active deployments with their newest LoRaWAN check-in from the `deployment_latest_lorawan` view, read-only (`lib/realtimeReads.ts`) |
| `/upload-data` | `UploadDataPage` (→ `UploadFlow`) | The single upload surface (the header **Upload** button, Home and the guide link here; demo users get `RequireNotDemo`) |
| `/manifest` | `ManifestPage` | Firmware `MANIFEST.zip` builder |
| `/upload-model` | `UploadModelPage` | Edge Impulse → Vela model upload (org managers) |
| `/clusters/:id` | `ClusterReviewPage` | Bulk-confirm HDBSCAN clusters, unconfirmed and biggest first, ten per page; a confirm offers Undo |
| `/review/:id` | `ReviewQueuePage` | Retired Review Queue: redirects to `/annotations?deployment=:id` |
| `/umap/:id` | `UmapExplorerPage` | Embedding-space map of a deployment; a click opens that photo in the grid |
| `/intelligence/:id` | `DatasetHealthPage` | Dataset health + QA agreement (per project), behind `FF_INTELLIGENCE_ENABLED`. Links to a flagged page show only when `useFeatureEnabled` says the feature answers |
| `/processing` | `ProcessingHistoryPage` | Upload & pipeline job history, filtered by status in the read, 50 at a time ("Load more"); `?job=<id>` opens that job's row |
| `/notifications` | `NotificationsPage` | Notification inbox, read **direct from Supabase** via `useNotifications` (there is no `/api/notifications` router), filtered in the query and paged with "Load more"; the unread count (a server-side count) drives the nav badge. Delivery/email config (`EMAIL_PROVIDER`, `RESEND_API_KEY`, …): [deployment-guide](../resources/deployment-guide.md#full-pipeline-config-checklist-per-subsystem) |
| `/settings` | `SettingsPage` | Projects (a row opens its settings in a slide-over), the account (change password) and integrations (iNaturalist, organisation API keys) |
| `/admin/usage` | `AdminUsagePage` | Per-user usage limits (platform admin) |
| `/admin/devices` | `AdminDevicesPage` | Every device with its latest deployment and that project's organisation, read-only (platform admin) |

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
services/  → infrastructure adapters (Supabase, Azure, Drive, Vela, SpeciesNet, http)
schemas/   → Pydantic request/response models
jobs/      → async job system (definitions, runner, store, ARQ worker)
middleware/→ request id, structured logging, rate limiting, CORS
registries/→ static config (camera configs, model + embedding registries)
```

**Routers** (`/api/*`, registered in `app/main.py`): `auth` (demo session), `admin` (system-admin
device list), `jobs`, `deployments`, `projects`, `exif`, `manifest`, `exports`, `reports` (the GBF
report, answering `FEATURE_DISABLED` without `FF_GBF_REPORT_ENABLED`), `models`, `media`,
`public_api`, `inaturalist`, `clustering`, and, each behind its feature flag, `camtrapdp`,
`pipeline`, `brain`
(embeddings/clusters/UMAP/similarity), `qa` (AI-vs-human agreement), `intelligence`
(dataset health/alerts).

**Key domain modules**: `exif`, `photo_preprocessing`, `pipeline` (SpeciesNet steps), `events`,
`clustering`, `wildlife_brain` + `embedding_lifecycle` + `active_learning` (the DINOv3 "Brain"),
`human_verdicts` (cluster confirm and undo, batch review: one human row per photo),
`intelligence`, `media_registry` / `media_resolver`, `model`, `manifest`, `camtrapdp`, `camtrapdp_export`, `inaturalist`,
`public_api`, `gbf_report` + `gbif_dataset` (the GBF report and a project's GBIF dataset).

### Adding a feature (backend)

1. **Schema** — Pydantic models in `schemas/`
2. **Domain** — logic in `domain/` (no HTTP imports)
3. **Router** — thin endpoint in `routers/` (validate → delegate)
4. **Register** — include the router in `app/main.py`
5. **Test** — add to `tests/`

See [03-DATA-AND-SYNC](./03-DATA-AND-SYNC.md) for the job system and the Supabase RLS/GRANT model,
and [04-AI-PIPELINE](./04-AI-PIPELINE.md) for the inference layer.
