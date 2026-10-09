# Documentation, validation and the commit-time check

Where each kind of document lives, what to run before you claim work is done, and the check
that keeps this agent layer from rotting.

## Development conversations and documentation

**Docs are the record, GitHub issues are the tracker.** The rules live in
[`documentation/development reports/README.md`](../../../documentation/development%20reports/README.md),
including how to file a finding and what to check before closing a thread. Read it before
starting or closing one.

Never leave substantive material only in a chat transcript, an email or a PR comment. An
investigation, a review exchange or a design decision belongs in a dated report. This is the
failure mode to watch for, because the work is done and the finding is real, and it
evaporates anyway because it only ever existed in a conversation.


## Documentation Invariant

When behavior changes:

* Update the relevant documentation
* Keep documentation and implementation synchronized
* Update route documentation when routes change
* Update API documentation when contracts change

Do not create duplicate documentation when existing documentation can be updated.

What a report or doc keeps: decisions (one line each, the reason in a clause), contracts
(columns, signals, weights, env vars, schemas), measured numbers with source and date, commands,
open items. What it cuts: background, restatements of another document, prose explaining
existing code (a `file::function` link does it), worked narratives. Each fact has one home;
everything else links to it. Before writing, decide which document owns each fact.


# 9. Validation Requirements

Before committing:

## Backend Changes

Run:

* Ruff linting
* Ruff formatting
* Pytest

## Frontend Changes

Run:

* ESLint
* TypeScript validation: `npx tsc -b --noEmit`, **not** `npx tsc --noEmit`. The root
  `tsconfig.json` is references-only (`"files": []`), so plain `tsc --noEmit` type-checks
  nothing and exits 0 with errors present. `npm run build` (`tsc -b && vite build`) catches
  them; so does the `-b` form on its own.
* Production build if applicable, then `npm run size`: the size budgets fail CI, not just the
  build

## Documentation changes

Run `node scripts/validate-docs.js` from the repo root after a rename or a move: it fails on any
repository path or relative link that no longer resolves, and CI runs it as the `Docs Links` job.

## General

Verify:

* Documentation is updated
* Feature flags are respected
* Shared contracts remain compatible
* No secrets were introduced

Do not commit changes that fail validation.

---

# 10. Development Principles

Prefer:

* Thin routers
* Small domain functions
* Reusable components
* Strong typing
* Explicit validation
* Real API testing
* Follow YAGNI (You Aren't Gonna Need It) principles
* Prefer simple, one-liner solutions where readable and appropriate

Avoid:

* Business logic in routers
* Duplicated logic
* Hardcoded configuration
* Architecture assumptions
* Unverified schema assumptions

---

## House style

- **No em dashes.** Use commas, or start a new sentence. This applies to every document and
  anything else that gets pasted somewhere: em dashes read as machine-written to the people who
  fund this work. Existing source comments still carry them; leave those alone unless you are
  already rewriting the file.
- **Verify against the code, not the docs**, and against `ww-backend` for anything about the
  database. Treat any undated claim as a hypothesis, and say when you checked.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) under
`@commitlint/config-conventional`, the same rules as ww-mobile-app
([`frontend/commitlint.config.js`](../../../frontend/commitlint.config.js)). The
`frontend/.husky/commit-msg` hook checks each commit and
[`commitlint.yml`](../../../.github/workflows/commitlint.yml) checks every commit in a pull
request; a dev to main release is skipped, its commits were checked on the way into dev. What
failed in the dev history when it was turned on (9 Oct 2026, 10 of the last 50):

- The type is one of `build chore ci docs feat fix perf refactor revert style test`. Not `ui`,
  `content` or `deps`: use `feat`, `fix`, `style`, `docs` or `build(deps)`.
- The subject starts lower case, proper nouns included: `ci(e2e): add Lighthouse budgets`, not
  `ci(e2e): Lighthouse budgets`.
- Every line, header, body and footer, is at most 100 characters. Wrap the body by hand.

Hooks run from `frontend/.husky/_`, which `npm install` in `frontend/` creates. A worktree with a
junctioned `node_modules` has none, so no hook runs there until you run
`frontend/node_modules/.bin/husky frontend/.husky` from its root.

## The commit-time check

Before every commit, look at what the change means for the agent layer, meaning `AGENTS.md`,
the skill and its reference files, and decide whether anything needs to be added, edited or
deleted. It takes a few seconds and it is what keeps this layer from rotting.

Three questions, in order:

1. **Did I learn something that would have saved me time today?** A trap, a contract, a
   dependency that behaves differently in Docker than locally. That belongs in
   [gotchas.md](gotchas.md) or the reference it fits, with the date and what it cost.
2. **Did I make something here wrong?** A moved route, a renamed hook, a flag that now lives
   somewhere else, a layering rule the code no longer follows. Fix the line that is now false
   in the same commit. A confidently wrong skill is worse than a thin one.
3. **Is something here now redundant?** A gotcha whose cause was fixed, a workaround for a
   dependency version nobody runs, a rule CI now enforces. Delete it, and say in the commit
   message what was removed and why. This layer grows by default; only deliberate pruning
   shrinks it.

If the answer to all three is no, commit and say nothing. The check is a habit, not a ritual to
document each time.
