# iNaturalist Integration (Stage 1 + Stage 2 Plan)

Last updated: 2026-04-09

This repo currently focuses on firmware/model tooling in Streamlit (see `app.py`) and Supabase utilities (see `db_utils.py`).

This document captures:

- What was implemented for **Stage 1** (iNaturalist OAuth login in Streamlit)
- The design and implementation plan for **Stage 2** (clustering + upload representatives + poll results)

---

## Goals

### User-facing goal
Enable a Wildlife Watcher user to:

1. Connect their **own** iNaturalist account from within the Streamlit app
2. Upload / select camtrap captures
3. (Stage 2) Reduce upload volume via clustering and send only representative images/events to iNaturalist
4. (Stage 2) Retrieve IDs/suggestions and show results in a table

### Non-goals (for now)
- Not building an embedded iNaturalist UI/widget (iNat doesn’t provide this as a drop-in component)
- Not asking for iNat username/password inside Streamlit (OAuth is used instead)

---

## Stage 1 — OAuth login (implemented)

### What Stage 1 does

- Adds a sidebar panel in Streamlit to **connect/disconnect iNaturalist**
- Performs the OAuth authorization code flow:
  - User clicks **Connect iNaturalist**
  - User authenticates/approves on `inaturalist.org`
  - iNat redirects the user back to the Streamlit app with `?code=...&state=...`
  - The app exchanges the code for a token and stores it in `st.session_state`

### Why Stage 1 needs `INAT_CLIENT_ID/SECRET`
OAuth requires your Streamlit app to be registered as an “application” with iNaturalist.

These values identify **your app**, not the end-user.

The end-user still logs in to iNat using **their own** credentials — your app never sees their password.

### Files added/changed

- `inat_oauth.py`
  - Minimal OAuth helpers:
    - build authorization URL
    - exchange code for token
    - optional PKCE support
- `app.py`
  - Adds `render_inat_oauth_panel()` and renders it in the sidebar

### In-app state
Stage 1 stores auth in session only:

- `st.session_state.inat_oauth["token"]` → token JSON (as returned by iNat)
- `st.session_state.inat_oauth["state"]` → CSRF state
- `st.session_state.inat_oauth["pkce_verifier"]` → PKCE verifier

This is intentional for early testing. Persisting tokens (e.g. in Supabase) can be added after the flow is stable.

### Required configuration
Set these in **Streamlit secrets** or environment variables:

- `INAT_CLIENT_ID`
- `INAT_CLIENT_SECRET`
- `INAT_REDIRECT_URI`
- `INAT_SCOPE` (optional, default is `write`)

#### Redirect URI guidance
`INAT_REDIRECT_URI` must match exactly what you register in the iNaturalist developer console.

Common values:

- Local dev: `http://localhost:8501/`
- Streamlit Cloud: `https://wildlifewatcher.streamlit.app/`

**Note:** Many OAuth providers require an exact match including trailing `/`.

### How to test Stage 1

1. Ensure the 3 required env vars are present.
2. Run the Streamlit app.
3. In the sidebar, click **Connect iNaturalist**.
4. Follow the auth prompt on iNat.
5. Confirm the sidebar shows “Logged in”.

---

## Stage 2 — Clustering + upload + results (planned)

Stage 2 will be built in two sub-stages so each step is testable.

### Stage 2A — Build the clustering/select-representatives pipeline

#### Goal
Reduce a camtrap dataset to a small set of representative images/events to avoid uploading thousands of near-duplicates.

#### Inputs
- A set of images (uploaded folder/zip, or fetched from storage)
- Optional metadata (EXIF datetime, lat/long, deployment id)

#### Outputs
A structured selection artifact such as:

- `cluster_id` per image
- `representative=True/False`
- `representative_reason` (e.g., medoid/quality)
- `distance_to_centroid` and/or `cluster_compactness`

#### Suggested approach
1. **Pre-group into “events”** by timestamp proximity (cheap, reduces volume fast)
2. **Compute embeddings** for images (foundation model features)
3. **Cluster embeddings** (e.g., HDBSCAN or agglomerative thresholding)
4. Choose **1–3 representatives** per cluster:
   - medoid (closest to centroid)
   - quality-weighted selection (sharpness, exposure, subject size)
5. Mark outliers/noise for optional upload (often “interesting” cases)

#### Testing
- Add a small CLI script (`scripts/cluster_images.py`) that:
  - runs embeddings + clustering on a folder
  - prints counts (N images → M clusters → R reps)
  - writes a CSV manifest of selections

This allows repeatable testing without iNat or Streamlit.

### Stage 2B — Upload selected representatives to iNat + poll for results

#### Goal
Use Stage 1 auth token to:

- Create observations and attach photos for representatives
- Poll for identifications / CV suggestions
- Render a results table

#### Data model (recommended)
Introduce a lightweight “job” record (eventually persisted in Supabase) that tracks:

- local image id/path
- cluster id
- representative flag
- iNat observation id + URL
- status (`queued/uploaded/pending_ids/identified/failed`)
- timestamps + error field

#### Upload flow
1. For each representative:
   - create observation (time + location + notes)
   - upload photo
2. Save IDs locally/in DB

#### Polling flow
- Fetch observation status every X seconds/minutes (throttled + cached)
- Extract:
  - community taxon
  - quality grade (`needs_id`, `research`, etc.)
  - recent identifications

#### Results table
A Streamlit table with:

- image thumbnail
- cluster id
- representative observation link
- predicted/community taxon
- confidence indicators:
  - cluster compactness
  - distance-to-representative
- propagation flag (rep result applied to non-representative cluster members)

#### Anti-spam safeguards
- Only upload reps
- Limit uploads/day
- Always show review UI before uploading
- Default geoprivacy to obscured/private when needed

---

## Notes / follow-ups

### Token persistence
Stage 1 uses in-session storage only. After Stage 1 is stable, persist iNat tokens per user:

- Prefer storing encrypted tokens in Supabase (server-side)
- Handle refresh/expiry and re-auth prompts

### Dependencies
Stage 1 uses `requests`. Stage 2 will add ML deps (timm/torch or a smaller embedding stack). We’ll keep this CPU-friendly by default for Streamlit Cloud.

---

## Quick reference: where to look in code

- OAuth helpers: `inat_oauth.py`
- Streamlit UI: `app.py`
- Supabase pagination helper: `db_utils.py`
