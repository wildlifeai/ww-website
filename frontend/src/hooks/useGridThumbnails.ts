// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridThumbnails: which of the review grid's cards have no thumbnail and are stuck rather than
// still on their way (#208), the backfill that makes them (Retry, and once by itself per
// deployment, #175), and the refreshes that show what a job made (#286).
import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import type { MediaRecord } from '../lib/gridMedia'
import { displayImageUrl } from '../lib/mediaImageUrl'
import { createLimiter } from '../lib/limiter'
import { THUMBNAIL_RETRY_CONCURRENCY, isThumbnailStuck } from '../lib/thumbnailRetry'
import { useAutoRetryThumbnails } from './useAutoRetryThumbnails'
import { useBusyDeployments } from './useBusyDeployments'
import { useRefreshWhileBusy } from './useRefreshWhileBusy'

// Backfills run two at a time, the rest queued, whichever Retry or the automatic retry asked (02 #23).
const backfills = createLimiter(THUMBNAIL_RETRY_CONCURRENCY)

interface Args {
  media: readonly MediaRecord[]
  failedThumbs: ReadonlySet<string>
  loadedAt: number
  shownDeploymentIds: readonly string[]
  reload: (countAgain: boolean) => void
  refreshQuietly: () => void
  onError: (message: string) => void
}

export function useGridThumbnails({ media, failedThumbs, loadedAt, shownDeploymentIds, reload, refreshQuietly, onError }: Args) {
  const qc = useQueryClient()
  // Deployments whose thumbnail backfill this page started, until the job list shows it.
  const [retryRequested, setRetryRequested] = useState<ReadonlySet<string>>(new Set())

  // Deployments with a queued or running job. When one finishes, reload so the
  // thumbnails and labels it produced appear without a manual refresh.
  const busy = useBusyDeployments(() => {
    setRetryRequested(new Set())
    reload(true)
  })
  // While a job runs on a deployment in view, refetch the page so its thumbnails appear (#286).
  useRefreshWhileBusy(shownDeploymentIds, busy, refreshQuietly)

  // The deployment's cards stop reading "No thumbnail" at once; its backfill waits its turn.
  const retryThumbnails = useCallback((deploymentId: string) => {
    setRetryRequested(s => new Set(s).add(deploymentId))
    void backfills(async () => {
      try {
        await apiClient.post(`/api/media/thumbnails/${deploymentId}`, {})
        await qc.invalidateQueries({ queryKey: ['jobs'] })
      } catch (e) {
        setRetryRequested(s => { const n = new Set(s); n.delete(deploymentId); return n })
        onError(`Couldn't start the thumbnails: ${e instanceof Error ? e.message : String(e)}`)
      }
    })
  }, [qc, onError])

  // Cards with no image to show: still processing, or stuck past the grace period (#208).
  const missingThumbs = media.filter(m => !retryRequested.has(m.deployment_id) && (!displayImageUrl(m, 'thumb') || failedThumbs.has(m.id)))
  // Start the backfill once by itself for a deployment whose cards say "No thumbnail" (#175).
  useAutoRetryThumbnails(missingThumbs, loadedAt, busy, retryThumbnails)
  // One notice with one Retry for the stuck deployments on the page, not a button per card (02 #22).
  const stuckThumbs = missingThumbs.filter(m => isThumbnailStuck(m, loadedAt, busy))
  const stuckDeploymentIds = [...new Set(stuckThumbs.map(m => m.deployment_id))]
  /** The card's thumbnail should exist by now and nothing is making it. */
  const isStuck = (m: MediaRecord) => !retryRequested.has(m.deployment_id) && isThumbnailStuck(m, loadedAt, busy)

  return { stuckCount: stuckThumbs.length, stuckDeploymentIds, isStuck, retryThumbnails }
}
