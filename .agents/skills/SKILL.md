---
name: ww-website
description: Map to the reference files (database and cross-repo boundaries, backend, frontend, environment and cloud access, gotchas, documentation) for the Wildlife Watcher website (React + Vite SPA, FastAPI backend, shared Supabase, AI annotation pipeline). Read after AGENTS.md, before changing code, infrastructure or docs in this repo.
---

# Wildlife Watcher website, working knowledge

Read [`AGENTS.md`](../../AGENTS.md) first. It holds the quickstart, the gates to run before you
claim work is done, the non-negotiables and where the documentation is; this file does not
repeat them. This file is the layer underneath, a map to the detail. The reference files hold
the things that have already cost someone a day, so read the one that matches your task rather
than all of them.

The `ww-website` repository is a multi-service platform consisting of:

* React + Vite frontend
* Python FastAPI backend
* Shared Supabase data layer
* Image analysis and AI tooling
* LoRaWAN telemetry display (ingest is ww-backend's `lorawan-ingest`)
* Firmware and manifest management workflows

## Read this one next

| If you are touching | Read |
|---|---|
| Anything that reads or writes the database, or another repo's data | [references/database-and-cross-repo.md](references/database-and-cross-repo.md) |
| `backend/`, an async job, a secret, or a feature flag | [references/backend.md](references/backend.md) |
| `frontend/`, a hook, or the API client | [references/frontend.md](references/frontend.md) |
| `.env`, Docker, line endings or file encoding | [references/environment-and-files.md](references/environment-and-files.md) |
| `infra/aws/`, an SSM parameter, an AWS deploy, or `cluster/reannz/` | [references/environment-and-files.md](references/environment-and-files.md) |
| Something that looks like a bug in your own code | [references/gotchas.md](references/gotchas.md) |
| LoRaWAN on cloud dev with a real camera, or the Realtime page against real messages | [`lorawan-dev-testing.md`](../../documentation/resources/lorawan-dev-testing.md) |
| Documentation, validation, commit messages, or your own commit hygiene | [references/documentation.md](references/documentation.md) |
| The UI design system | [`.agents/DESIGN.md`](../DESIGN.md) |
| Writing a user guide | [`guide-author/SKILL.md`](guide-author/SKILL.md) |
