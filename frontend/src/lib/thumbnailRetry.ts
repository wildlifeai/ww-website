// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// When a grid card with no thumbnail is "still processing" and when it is stuck (#208).
// A stuck card offers Retry, which runs the deployment's thumbnail backfill; the grid also
// starts that backfill once by itself per deployment and browser session (#175).

/** How long after a photo is registered its thumbnail may still legitimately be on its way. */
export const THUMBNAIL_GRACE_MS = 10 * 60 * 1000

/**
 * True when a photo's thumbnail should exist by now but doesn't: it was registered more than
 * the grace period before `now`, and no running job covers its deployment. A photo with no
 * `created_at` counts as old.
 */
export function isThumbnailStuck(
  media: { deployment_id: string; created_at?: string | null },
  now: number,
  busyDeploymentIds: ReadonlySet<string>,
): boolean {
  if (busyDeploymentIds.has(media.deployment_id)) return false
  if (!media.created_at) return true
  const created = Date.parse(media.created_at)
  return Number.isNaN(created) || now - created > THUMBNAIL_GRACE_MS
}

/** Deployments with a queued or running job, so their cards keep saying "Processing…". */
export function busyDeployments(
  jobs: ReadonlyArray<{ status: string; deployment_ids?: string[] | null }> | undefined,
): Set<string> {
  const busy = new Set<string>()
  for (const j of jobs ?? []) {
    if (j.status === 'queued' || j.status === 'processing') (j.deployment_ids ?? []).forEach(id => busy.add(id))
  }
  return busy
}

/**
 * Deployments with a card showing "No thumbnail", sorted: the grid starts their backfill once
 * by itself (#175). `cards` are the photos with no image to show and no Retry requested yet.
 */
export function stuckDeployments(
  cards: ReadonlyArray<{ deployment_id: string; created_at?: string | null }>,
  now: number,
  busyDeploymentIds: ReadonlySet<string>,
): string[] {
  const stuck = new Set<string>()
  for (const c of cards) if (isThumbnailStuck(c, now, busyDeploymentIds)) stuck.add(c.deployment_id)
  return [...stuck].toSorted()
}

/**
 * The stuck deployments not yet retried automatically in this browser session. One automatic
 * request per deployment, so a photo whose thumbnail cannot be made never loops; Retry stays
 * for later attempts.
 */
export function dueForAutoRetry(stuck: ReadonlyArray<string>, alreadyRetried: ReadonlySet<string>): string[] {
  return stuck.filter(id => !alreadyRetried.has(id))
}

/** sessionStorage key holding the deployments already retried automatically. */
export const AUTO_RETRY_KEY = 'ww:thumbnailAutoRetry'

/** The deployments `storage` says were retried automatically; empty when it is missing, unreadable or corrupt. */
export function readAutoRetried(storage: Pick<Storage, 'getItem'> | null): Set<string> {
  try {
    const ids: unknown = JSON.parse(storage?.getItem(AUTO_RETRY_KEY) ?? '[]')
    return new Set(Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : [])
  } catch {
    return new Set()
  }
}

/** Record the automatically retried deployments; a storage that refuses the write is ignored. */
export function rememberAutoRetried(storage: Pick<Storage, 'setItem'> | null, ids: ReadonlySet<string>): void {
  try {
    storage?.setItem(AUTO_RETRY_KEY, JSON.stringify([...ids]))
  } catch {
    // Private mode or a full quota: the in-memory record still holds for this page load.
  }
}

/** How often the grid refetches its page while a job runs on a deployment in view (#286). */
export const GRID_REFRESH_MS = 30_000

/** True when any deployment the grid shows has a queued or running job. */
export function showsBusyDeployment(shownDeploymentIds: ReadonlyArray<string>, busyDeploymentIds: ReadonlySet<string>): boolean {
  return shownDeploymentIds.some(id => busyDeploymentIds.has(id))
}
