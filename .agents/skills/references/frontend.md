# Frontend architecture

Hooks, the API client, and the abstractions to reuse rather than duplicate.

# 4. Frontend Architecture Rules

Preferred flow:

```text
pages
  ↓
components
  ↓
hooks
  ↓
apiClient
```

Rules:

* Use `apiClient` for backend communication
* Use TanStack Query for server state
* Keep route components thin
* Prefer reusable components
* Prefer hooks for complex state management
* Avoid direct `fetch()` calls when `apiClient` already provides the functionality
* Avoid duplicating API response parsing logic
* `useAuth` keeps one `user` object per signed-in person (`lib/authUser.ts`): Supabase re-sends
  the session on every token refresh, and a new object reset every effect keyed on `user`
  (#154). Keep that guard, and key new effects on `user?.id` where the object is not needed.
* A page filtered by the top-bar project picker reads `queryProjectIds` and `noProjectSelected`
  from `useProjectSelection` and renders `NoProjectSelected` when nothing is ticked. An empty
  selection means none, never all; the rule lives in `lib/projectSelection.ts` (#214). Sign-in
  selects one project, the one whose newest deployment started last (`defaultProjectId`), read
  in the same request as the project list.
* The project list refetches when the user returns to the tab, at most every 30 s
  (`lib/tabReturnRefresh.ts`), without a loading state. A website write that creates, deletes,
  restores or renames a project calls `reloadProjects()`, or the top-bar and upload pickers miss
  it (#299).
* What the user may do in a project comes from `useProjectRoles()` (`canView`, `canWrite`,
  `isProjectAdmin`, `isOrgManager`, `managesAnyOrg`, `isSystemAdmin`, `loading`), one shared
  query of the user's own `user_roles`, with the rules in `lib/projectRoles.ts` mirroring
  `has_project_role`. Hide what the role cannot do rather than show it and fail; don't read
  `user_roles` again in a component. `useIsAdmin` is a thin wrapper over it.
* Every Supabase update asks for the changed rows (`.select('id')`) and treats none as a refusal:
  RLS refuses an update as "0 rows, no error". Observations go through `updateObservations` and
  `deleteObservation` (`lib/observationWrites.ts`), which throw on 0 rows.
* Read observations for a count, chart or species list through `fetchLiveObservations`
  (`lib/liveObservations.ts`), never a bare `from('observations')`: a deleted photo leaves its
  observations behind ([03-DATA-AND-SYNC](../../../documentation/onboarding/03-DATA-AND-SYNC.md), #198).
* A route whose feature flag is off answers HTTP 200 with `FEATURE_DISABLED`, which `apiClient`
  throws. Show a link to a flagged page (Dataset Health, UMAP, Cluster Review) only when
  `useFeatureEnabled(feature, id)` is true: one cached probe per feature, not one per row
  (`lib/featureFlags.ts`, UI audit decision 6).
* A route that raises `HTTPException` (every `/api/inat` route) reaches `apiClient` as code
  `UNKNOWN` with the `detail` as its message, and the status is lost, so match the message
  (`lib/inatOff.ts`, `inatErrorNotice` in `lib/inatPublish.ts`).
* React Doctor fails a PR that introduces any warning (`react-doctor.yml`, `blocking: warning`,
  scope `changed`). Check before pushing, from `frontend/`:
  `npx -y react-doctor@0.9.12 . --scope changed --base origin/dev --verbose --yes`. Rules
  switched off for the repo are in `frontend/doctor.config.json`. Its score is not the gate.

Frontend should focus on presentation and user interaction rather than business logic.
