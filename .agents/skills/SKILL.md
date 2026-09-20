---
name: ww-website
description: Workflow, layering rules and cross-repo boundaries for the Wildlife Watcher website (React + Vite SPA, FastAPI backend, shared Supabase, AI annotation pipeline). Read before changing code, infrastructure or docs in this repo.
---

# Wildlife Watcher website, working knowledge

Read [`AGENTS.md`](../../AGENTS.md) first for the quickstart. This file is the layer
underneath: the rules that apply to any change, and a map to the detail. The reference files
hold the things that have already cost someone a day, so read the one that matches your task
rather than all of them.

# Wildlife Watcher Website Agent

The `ww-website` repository is a multi-service platform consisting of:

* React + Vite frontend
* Python FastAPI backend
* Shared Supabase data layer
* Image analysis and AI tooling
* LoRaWAN telemetry ingestion
* Firmware and manifest management workflows

> **Canonical Documentation**
>
> Always consult the relevant documentation before making assumptions:
>
> * `readme.md`
> * `documentation/onboarding/02-CODEBASE-GUIDE.md`
> * `documentation/onboarding/03-DATA-AND-SYNC.md`
> * `documentation/resources/api-reference.md`
> * `documentation/resources/deployment-guide.md`
> * `documentation/resources/cloud-infrastructure.md` — what actually exists in Azure/Supabase/Cloudflare
> * `documentation/README.md` — the index; says which docs are **living** vs **frozen history**
> * `.agents/DESIGN.md` — design system (colors, typography, spacing, component patterns) for any UI work or screen generation
>
> This skill defines architectural guardrails and decision rules. Detailed implementation guidance belongs in documentation.

---

## Read this one next

| If you are touching | Read |
|---|---|
| Anything that reads or writes the database, or another repo's data | [references/database-and-cross-repo.md](references/database-and-cross-repo.md) |
| `backend/`, an async job, a secret, or a feature flag | [references/backend.md](references/backend.md) |
| `frontend/`, a hook, or the API client | [references/frontend.md](references/frontend.md) |
| `.env`, Docker, line endings or file encoding | [references/environment-and-files.md](references/environment-and-files.md) |
| Something that looks like a bug in your own code | [references/gotchas.md](references/gotchas.md) |
| Documentation, validation, or your own commit hygiene | [references/documentation.md](references/documentation.md) |
| The UI design system | [`.agents/DESIGN.md`](../DESIGN.md) |
| Writing a user guide | [`guide-author/SKILL.md`](guide-author/SKILL.md) |

## Workflow

- **Never commit or push to a shared branch without asking the maintainer.**
- **Check the agent layer before each commit.** Decide whether this change makes anything in
  `AGENTS.md`, this file or a reference file wrong, missing or redundant, and fix it in the
  same commit. The three questions are in
  [references/documentation.md](references/documentation.md).
- Before you claim work is done, run the gates:
  `cd backend && ruff check . && ruff format --check . && pytest`, then
  `cd frontend && npm run lint && npx tsc --noEmit && npm run build`.
- **Verify against the code, not the docs**, and against `ww-backend` for anything about the
  database. Column names guessed from memory are a recurring source of failures here.

## The five that apply to almost any change

1. **This repo does not own the database.** Schema, RLS policies and table GRANTs live in
   `ww-backend`. Add a migration there, then consume it.
2. **Backend layering is `routers` to `domain` to `services`.** No FastAPI or HTTP imports in
   `domain/`, no business logic in `routers/`.
3. **The service-role key is backend-only.** Never expose it to frontend code.
4. **Gate unfinished work behind a feature flag**, and set AI flags on the ARQ worker as well
   as the API, or the pipeline silently no-ops.
5. **Every file is LF, UTF-8, no BOM.** A diff where insertions are about equal to deletions is
   flipped line endings, not an edit.

## Agent self-check

Before making a change, verify:

### Database

* Am I avoiding schema changes in this repository?
* Have I checked ownership of the affected data model?

### Backend

* Am I respecting `routers → domain → services`?
* Have I avoided HTTP imports in domain code?

### Frontend

* Am I using existing hooks and apiClient abstractions?
* Am I avoiding duplicated API logic?

### Security

* Am I keeping service-role access backend-only?
* Have I avoided exposing secrets?

### Documentation

* Does documentation need updating?
* Have I updated it alongside the code?

### Validation

* Have I run the appropriate tests and linting?

If any answer is "no", stop and correct the issue before proceeding.
