// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useAutoRetryThumbnails: when the grid shows "No thumbnail" cards, start the deployment's
// thumbnail backfill once by itself, so photos registered without a rendition (#175) are made
// without anyone pressing Retry. Each deployment is retried automatically at most once per
// browser session (lib/thumbnailRetry), so one whose thumbnails cannot be made never loops.
import { useEffect, useEffectEvent } from 'react'
import { useJobsList } from './useJobs'
import { dueForAutoRetry, readAutoRetried, rememberAutoRetried, stuckDeployments } from '../lib/thumbnailRetry'

// Survives the grid unmounting; sessionStorage carries it across a reload when it is allowed.
const retriedThisLoad = new Set<string>()

function sessionStore(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function useAutoRetryThumbnails(
  cards: ReadonlyArray<{ deployment_id: string; created_at?: string | null }>,
  now: number,
  busyDeploymentIds: ReadonlySet<string>,
  retry: (deploymentId: string) => void,
): void {
  // Until the job list arrives every deployment looks idle, and a running job would be doubled.
  const { isSuccess: jobsLoaded } = useJobsList()
  const stuckKey = jobsLoaded ? stuckDeployments(cards, now, busyDeploymentIds).join(',') : ''
  const start = useEffectEvent(retry)

  useEffect(() => {
    const stuck = stuckKey.split(',').filter(Boolean)
    if (stuck.length === 0) return
    const storage = sessionStore()
    const retried = new Set([...retriedThisLoad, ...readAutoRetried(storage)])
    const due = dueForAutoRetry(stuck, retried)
    if (due.length === 0) return
    for (const id of due) { retried.add(id); retriedThisLoad.add(id) }
    rememberAutoRetried(storage, retried)
    due.forEach(id => start(id))
  }, [stuckKey])
}
