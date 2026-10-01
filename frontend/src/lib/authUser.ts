import type { AuthChangeEvent, User } from '@supabase/supabase-js'

/**
 * The user `useAuth` should hold after an auth event: the previous object while the same person
 * is signed in.
 *
 * Supabase re-sends the session on every token refresh, which happens when a tab comes back into
 * focus near the hourly expiry and when another tab refreshes it. Each time it is a new `User`
 * object for the same person, and every effect keyed on `user` ran again: the Annotations page
 * reloaded its deployments, swapped the grid for "Loading deployments…" and lost the selection,
 * the filters and the open photo (#154). A real change of person, a sign-out, or an update to the
 * user's own record still takes the new value.
 */
export function nextAuthUser(prev: User | null, next: User | null, event?: AuthChangeEvent): User | null {
  if (event === 'USER_UPDATED') return next
  return prev && next && prev.id === next.id ? prev : next
}
