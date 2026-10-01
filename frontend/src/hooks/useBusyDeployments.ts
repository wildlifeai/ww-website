// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useBusyDeployments: deployments the signed-in user has a queued or running job on, from
// the polled job list. Calls `onFinished` when a deployment drops off that list, so a view
// can reload and show what the job produced (thumbnails, labels).
import { useEffect, useEffectEvent, useMemo, useRef } from 'react'
import { useJobsList } from './useJobs'
import { busyDeployments } from '../lib/thumbnailRetry'

export function useBusyDeployments(onFinished: () => void): Set<string> {
  const { data: jobs } = useJobsList()
  const busy = useMemo(() => busyDeployments(jobs), [jobs])
  const busyKey = [...busy].toSorted().join(',')
  const previous = useRef(busyKey)
  const finished = useEffectEvent(onFinished)

  useEffect(() => {
    const now = new Set(busyKey.split(',').filter(Boolean))
    const before = previous.current.split(',').filter(Boolean)
    previous.current = busyKey
    if (before.some(id => !now.has(id))) finished()
  }, [busyKey])

  return busy
}
