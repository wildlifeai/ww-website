# E2E checks — dev website

Browser-level Playwright checks that the deployed (or local) dev stack works
end-to-end. They complement `backend/tests/` (pytest unit/domain tests): these
drive the real UI against a real database.

## What is covered

| Spec | Proves |
|---|---|
| `01-smoke` | Site + API up; public pages (`/`, `/guides`, `/faq`, `/resources`) render clean; seeded user can log in and every authed page (`/toolkit`, `/field`, `/annotations`, `/insights`, `/settings`) renders without errors or auth bounces |
| `02-demo` | "Try the demo" opens a signed-in session via `/api/auth/demo-session`; DemoGuard keeps it read-only (upload gated) |
| `03-upload-exif` | Real SD-card folder upload through the modal (webkitdirectory); EXIF UserComment deployment UUIDs bind exact-match to seeded deployments; a stamped UUID with **no** deployments row is auto-created (#92) so nothing lands unassigned |
| `04-lorawan-realtime` | With the Field page open, a synthetic `lorawan_messages`+`lorawan_parsed_messages` insert appears in the UI **without reload** — verifies both the FieldPage subscription (#94) and that the realtime publication SQL is applied to the dev DB |
| `05-a11y` | The public pages (`/`, `/login`, `/signup`, `/guides`, `/faq`, `/resources`) have no serious or critical axe-core violations (WCAG 2.0 and 2.1, A and AA). No account needed |

## Running

```bash
cd e2e
npm install && npx playwright install chromium
cp .env.example .env   # fill in target + creds
npm run e2e            # or e2e:smoke / e2e:demo / e2e:upload / e2e:lorawan / e2e:a11y
```

Specs self-skip when their env is missing, so a partial `.env` still gives a
useful (smaller) run. Point `E2E_BASE_URL`/`E2E_API_URL` at either the local
stack (`docker-compose.dev.yml` + `frontend: npm run dev`) or the deployed dev
site. The upload and realtime specs **write to the target database** — run them
against dev only, never production.

First run note: the selectors use roles/text and were written from the source;
if the UI has drifted, expect one calibration pass (traces/screenshots are
retained on failure — `npm run report`).

## In CI

`.github/workflows/e2e.yml` runs on every successful Cloudflare Pages deployment except
production (Cloudflare posts a GitHub deployment per branch build, and the workflow takes the
preview URL from it), on a pull request that changes `e2e/` or the workflow (against the dev
preview, since the specs are what changed), and by hand with a base URL and a suite.

- **E2E Smoke** runs `01-smoke` and `02-demo` against the preview, signed in as
  `E2E_EMAIL` (a repository variable, `tui@ww.org` by default) with the `dev` environment's
  `E2E_PASSWORD` secret, falling back to its `DEMO_PASSWORD`: every seeded user shares
  ww-backend's seed password, which the backend deploy already keeps there for the demo
  account. The job stops with a clear message when neither is set, except on a Dependabot pull
  request: a run Dependabot triggers sees only Dependabot secrets, and the seed password stays
  out of them because that run executes the package versions being bumped. There the sign-in
  check is skipped with a notice, the other smoke and demo checks still gate the bump, and the
  sign-in check runs on dev once the merge deploys.
- **E2E Full**, the same job with the `full` suite, adds `03` and `04`, which write to the dev
  database and storage; the LoRaWAN spec takes the `dev` environment's `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY`. It runs against the dev preview and the dev API after each
  successful push deploy of the dev backend (`deploy-backend.yml`), nightly at 14:17 UTC
  (03:17 NZDT, 02:17 NZST), and by hand. Both automatic triggers fire only from the copy of the
  workflow on `main`. Full runs queue rather than overlap. Each one ends with
  `cleanup.mjs`, pass or fail, and writes the test counts and what it deleted to the job
  summary. A red full run is a notification, nothing waits on it.

  The cleanup finds a run's rows by markers the run owns. LoRaWAN rows carry the run's tag
  (`e2e-ci-<run id>-<attempt>`) as their `device_eui`; marked rows over an hour old from a
  run that died go too. The upload's media rows cannot carry a tag (the backend names them),
  so they are the rows created since the run started, on Drive, in a deployment whose id starts
  with one of the fixture SD card's folder names; their observations, events and renditions go
  with them. The Drive originals stay: the `dev` environment has no Drive credentials, and Drive
  dedups by content, so runs do not add copies. The script refuses any Supabase project but
  dev, and `npm run test:cleanup` unit-tests its filters (on a pull request that changes `e2e/`).
  `node cleanup.mjs --dry-run` lists what it would delete.
- **E2E Full Stack** (`.github/workflows/e2e-full-stack.yml`, #216) runs `01-smoke` and
  `02-demo` against a stack started on the runner, on a pull request that changes `backend/`,
  `frontend/`, `e2e/` or the workflow: a local Supabase from ww-backend's `dev` migrations and
  seed, the pull request's backend under `uvicorn` on `:8000` and its frontend from
  `vite preview` on `:4173`. It is the only job that tests a pull request's backend through the
  UI before merge. The job picks a fresh seed password per run, so `tui@ww.org` and the demo
  account (`demo@wildlife.ai`, which the backend gets as `DEMO_EMAIL`) sign in with a value that
  only exists on the runner. Its one secret is `WW_BACKEND_READ_TOKEN`, for the ww-backend
  checkout. It fails on any skipped test, and on failure the `e2e-full-stack-<sha>` artifact
  holds the report, the screenshots and the backend and `vite preview` logs. To reproduce it
  locally, follow its steps in order: the workflow is the recipe.
- **A11y of public pages** runs `05-a11y` with no account and fails on a serious or critical
  violation. It was advisory until #213 cleared the colour-contrast findings (#212).
- **Lighthouse of public pages** audits `/`, `/login`, `/guides`, `/faq` and `/resources` three
  times each on the desktop preset, against the budgets in `lighthouserc.json`: performance 80,
  accessibility 95, best practices 90. The SEO score is collected and shown in the report but not
  asserted: Cloudflare preview deployments send `X-Robots-Tag: noindex`, which caps it near 50
  whatever the page does. Advisory for now (#228): a page under budget is a
  warning annotation on the run and the HTML reports are in the `lighthouse-<sha>` artifact,
  with the performance audits naming what to fix (the main chunk first). Blocking once the pages
  meet the budgets, the way the a11y job went.

The two Playwright jobs fail when the junit report holds nothing but skipped tests: the specs skip themselves
when their env is missing, so a missing secret would otherwise read as a pass. The dev API
scales to zero, so the first smoke test polls `/docs` for up to three minutes and reports how
long the cold start took.

Reading a failure: the job log. `helpers/test.ts` prints the failing test's URL, the page's
console errors and its visible text, and the `e2e-smoke-<sha>` artifact holds the screenshots
and the HTML report. Traces and videos are off in CI on purpose: a trace records every value
typed into a field, the password included, and the artifacts of a public repository are
downloadable by anyone signed in to GitHub.

Locally, `CI=true npx playwright test ...` gives the same reporter and settings.

## Manual checklist (not automatable from here)

- [ ] Realtime publication SQL applied to the **live dev** DB after #94 merges
      (apply manually — a `supabase/**` push to backend dev resets the DB)
- [ ] CF Pages build env still injects the intended Supabase anon key (no
      service-role key in the public bundle)
- [ ] Dev uploads storage account (`wwuploadsae`) reachable from the backend
- [ ] Demo account enabled on the dev backend (`/api/auth/demo-session` 200)
- [ ] After a backend-dev DB reseed: re-run `03-upload-exif` (seeds restore the
      fixture deployment UUIDs) and re-apply the publication SQL if the reset
      dropped it
- [ ] LoRaWAN end-to-end with real hardware: bench WW500 uplink → TTN →
      lorawan-ingest → Field page (the spec only covers DB → UI)
