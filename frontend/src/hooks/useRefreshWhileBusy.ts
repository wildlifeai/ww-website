// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useRefreshWhileBusy: while a job runs on a deployment the grid shows, call `refresh` every
// GRID_REFRESH_MS so thumbnails appear as the job makes them, and stop when it is idle (#286).
import { useEffect, useEffectEvent } from 'react'
import { GRID_REFRESH_MS, showsBusyDeployment } from '../lib/thumbnailRetry'

export function useRefreshWhileBusy(
  shownDeploymentIds: ReadonlyArray<string>,
  busyDeploymentIds: ReadonlySet<string>,
  refresh: () => void,
): void {
  const active = showsBusyDeployment(shownDeploymentIds, busyDeploymentIds)
  const tick = useEffectEvent(refresh)

  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => tick(), GRID_REFRESH_MS)
    return () => window.clearInterval(t)
  }, [active])
}
