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
  account. The job stops with a clear message when neither is set. The `full` suite, by hand only, adds `03` and `04`, which
  write to the dev database and storage; the LoRaWAN spec takes the `dev` environment's
  `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
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
