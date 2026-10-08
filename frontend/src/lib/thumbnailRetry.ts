// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// When a grid card with no thumbnail is "still processing" and when it is stuck (#208).
// A stuck card offers Retry, which runs the deployment's thumbnail backfill.

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

/** How often the grid refetches its page while a job runs on a deployment in view (#286). */
export const GRID_REFRESH_MS = 30_000

/** True when any deployment the grid shows has a queued or running job. */
export function showsBusyDeployment(shownDeploymentIds: ReadonlyArray<string>, busyDeploymentIds: ReadonlySet<string>): boolean {
  return shownDeploymentIds.some(id => busyDeploymentIds.has(id))
}
