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

/**
 * Whether the visitor may be signed in, known before Supabase answers: a stored session, or a
 * sign-in redirect (`#access_token=`, `?code=`) in the URL. False is signed out for certain, so a
 * public page can render at once instead of a "Loading…" it then replaces, a layout shift
 * Lighthouse counts (#228). Unsure, for example with storage blocked, it says true.
 */
export function mayHaveSession(
  storage?: Pick<Storage, 'length' | 'key'>,
  url?: { search: string; hash: string },
): boolean {
  try {
    const store = storage ?? window.localStorage
    const { search, hash } = url ?? window.location
    if (/[#&?](access_token|code)=/.test(hash + search)) return true
    for (let i = 0; i < store.length; i++) {
      if (/^sb-.+-auth-token$/.test(store.key(i) ?? '')) return true
    }
    return false
  } catch {
    return true
  }
}
