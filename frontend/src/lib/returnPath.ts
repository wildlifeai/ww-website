/**
 * Where to go after signing in (UI audit 05 #19): the page a signed-out visitor asked for, when
 * `RequireAuth` sent them to /login with `state={{ from: location }}`, otherwise home.
 *
 * Only a path on this site counts, never another origin, and never a sign-in page itself.
 */
export function returnPath(state: unknown): string {
  const from = (state as { from?: unknown } | null | undefined)?.from
  const path = typeof from === 'string' ? from : pathOf(from)
  if (!path || !path.startsWith('/') || path.startsWith('//') || path.startsWith('/\\')) return '/'
  if (/^\/(login|signup|reset-password)(?=[/?#]|$)/.test(path)) return '/'
  return path
}

function pathOf(from: unknown): string | null {
  if (!from || typeof from !== 'object') return null
  const { pathname, search, hash } = from as { pathname?: unknown; search?: unknown; hash?: unknown }
  if (typeof pathname !== 'string') return null
  return pathname + (typeof search === 'string' ? search : '') + (typeof hash === 'string' ? hash : '')
}
