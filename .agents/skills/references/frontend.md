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

Frontend should focus on presentation and user interaction rather than business logic.
