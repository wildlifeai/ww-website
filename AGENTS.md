# Agent guide, Wildlife Watcher website

The Wildlife Watcher web platform: a **React + Vite** SPA and a **FastAPI** backend over a
**shared Supabase** database, plus the AI pipeline that annotates camera-trap images
(SpeciesNet / BioCLIP / DINOv3) and the tooling that builds camera SD-card manifests.

**Before changing code, infrastructure or docs, read
[`.agents/skills/SKILL.md`](.agents/skills/SKILL.md)**: the workflow rules, the five that apply
to almost any change, and a map to the reference files that carry the detail (the database and
cross-repo boundaries, backend, frontend, environment and files, gotchas, documentation).
This file is only the quickstart.

## Run it

Both services share **one `.env` at the repo root** (`frontend/vite.config.ts` loads `../`;
the backend reads `../.env` before `backend/.env`). Node 20+, Python 3.11+.

```bash
cd backend  && python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev                        # :5173, API on :8000
```

Full-stack in Docker, **always pass both compose files**, or the Drive credential mount is
missing and uploads fail to authenticate:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Setup detail, env reference and a verification checklist:
[`documentation/onboarding/00-GETTING-STARTED.md`](documentation/onboarding/00-GETTING-STARTED.md).

## Validate before committing

```bash
cd backend  && ruff check . && ruff format --check . && pytest
cd frontend && npm run lint && npx tsc -b --noEmit && npm run build
```

`tsc -b`, not `tsc`: the root `tsconfig.json` is references-only, so plain `tsc --noEmit`
checks nothing and exits 0 with errors present.

## Non-negotiables

- **Ask the maintainer before committing or pushing** to any shared branch.
- **Check the agent layer before each commit.** Ask whether the change makes anything in this
  file, the skill or its reference files wrong, missing or redundant, then add, edit or delete
  in the same commit. The three questions are in
  [`.agents/skills/references/documentation.md`](.agents/skills/references/documentation.md).
  A confidently wrong skill costs more than a thin one.
- **No em dashes** in documents or anything else that gets pasted elsewhere. Commas, or a new
  sentence.
- **This repo does not own the database.** Schema, RLS policies **and** table GRANTs live in
  [`ww-backend`](https://github.com/wildlifeai/wildlife-watcher-backend) under
  `supabase/schemas/`. Never create or alter tables, columns or functions from here. Add a
  `ww-backend` migration, then consume it. Verify column names against that repo rather than
  guessing.
- **Backend layering is `routers → domain → services`.** No FastAPI or HTTP imports in
  `domain/`; no business logic in `routers/`.
- **The service-role key is backend-only.** Never expose it to frontend code, never commit
  secrets, never bypass the auth dependencies.
- **Gate unfinished work behind a feature flag** in `backend/app/config.py`, and set AI flags
  on the **ARQ worker**, not just the API, or the pipeline silently no-ops.
- **Every file is LF, UTF-8, no BOM.** `.gitattributes` enforces it; PowerShell's
  `Out-File`/`Set-Content` will quietly rewrite a whole file to CRLF, so don't edit through them.
- **Update the docs with the change.** Route changes update the codebase guide; API changes
  update the API reference. Prefer editing an existing doc over adding a new one.
- **Docs are the record, GitHub issues are the tracker.** Substantive findings go in
  [`documentation/development reports/`](documentation/development%20reports/README.md); anything
  still open becomes an issue on the
  [project board](https://github.com/orgs/wildlifeai/projects/3).
- **Shared contracts are cross-repo.** EXIF fields, op-parameters, LoRaWAN payloads and
  `CONFIG.TXT` belong to the firmware and `ww-hardware` repos; the mobile app shares this
  database. Never change one unilaterally.

## Where things are

| | |
|---|---|
| Start here, in order | [`documentation/onboarding/00`–`05`](documentation/onboarding/) |
| Doc index, what's living vs frozen | [`documentation/README.md`](documentation/README.md) |
| Deep agent rules | [`.agents/skills/SKILL.md`](.agents/skills/SKILL.md) |
| UI design system | [`.agents/DESIGN.md`](.agents/DESIGN.md) |
| What actually exists in Azure/Supabase/Cloudflare | [`documentation/resources/cloud-infrastructure.md`](documentation/resources/cloud-infrastructure.md) |
| API endpoints | [`documentation/resources/api-reference.md`](documentation/resources/api-reference.md) (authoritative: `/docs`) |
| How the code got this way | [`documentation/development reports/`](documentation/development%20reports/README.md) |
| Frozen history, read for *why*, never as current behaviour | `documentation/development reports/_archive/` |
