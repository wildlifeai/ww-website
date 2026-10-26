/**
 * useIsAdmin: true when the signed-in user holds the system-wide `ww_admin`
 * role. Read through `useProjectRoles`, so it shares that hook's one read of the
 * user's own roles. Used to gate admin-only navigation and pages (e.g. the Usage
 * dashboard).
 */
import { useProjectRoles } from './useProjectRoles'

// Returns null while resolving, then true/false, so callers can gate admin-only
// API calls and redirect immediately without a flash or wasted request. Null also
// while the session restores: resolving to false then bounced admins off
// /admin/usage before the role check could even start.
export function useIsAdmin(): boolean | null {
  const { loading, isSystemAdmin } = useProjectRoles()
  return loading ? null : isSystemAdmin
}
