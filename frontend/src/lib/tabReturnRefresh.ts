/**
 * Refetch a list when the user comes back to the tab (#299).
 *
 * The project list loads once per sign-in, so a project that syncs from the mobile app did not
 * reach the picker until a full reload (#152). Returning to the tab refetches it, at most once
 * per `TAB_RETURN_REFRESH_MS`: switching tabs fires both `visibilitychange` and `focus`.
 */

export const TAB_RETURN_REFRESH_MS = 30_000

/** True when the last load started at least `intervalMs` ago, or never happened. */
export function shouldRefreshOnReturn(
  lastLoadAt: number | null,
  now: number,
  intervalMs: number = TAB_RETURN_REFRESH_MS,
): boolean {
  return lastLoadAt === null || now - lastLoadAt >= intervalMs
}

type Listenable = Pick<EventTarget, 'addEventListener' | 'removeEventListener'>

/**
 * Call `onReturn` when the window regains focus or the document becomes visible again.
 * Returns the unsubscribe function, for an effect's cleanup.
 */
export function subscribeToTabReturn(
  onReturn: () => void,
  win: Listenable = window,
  doc: Listenable & { visibilityState: DocumentVisibilityState } = document,
): () => void {
  const onVisibility = () => { if (doc.visibilityState === 'visible') onReturn() }
  win.addEventListener('focus', onReturn)
  doc.addEventListener('visibilitychange', onVisibility)
  return () => {
    win.removeEventListener('focus', onReturn)
    doc.removeEventListener('visibilitychange', onVisibility)
  }
}
