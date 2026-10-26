// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridMedia: one page of the review grid's photos. Every filter is part of the query
// (lib/gridFilters), so the page and the total agree; the total is counted once per filter set
// (and again after a write), not on every page turn or refresh (UI audit 02 #7). While an upload
// runs the page is read again every 4 s, and once more, counted again, when it finishes.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { User } from '@supabase/supabase-js'
import { supabase } from '../config/supabase'
import { applyFilterOps, applyOrder, gridSortPlan, type FilterPlan, type GridSort } from '../lib/gridFilters'
import { MEDIA_SELECT, type MediaRecord } from '../lib/gridMedia'
import { PAGE_SIZE } from '../lib/gridPage'

export interface GridQuery {
  user: User | null
  /** The deployments in view; none reads nothing. */
  deploymentIds: readonly string[]
  /** Changes exactly when the rows the query can return change (`gridQueryKey`). */
  queryKey: string
  /** Which photos: the picked deployments or the selected projects (`gridScopePlan`). */
  scope: FilterPlan
  plan: FilterPlan
  sort: GridSort
  page: number
  uploadActive: boolean
}

export function useGridMedia({ user, deploymentIds, queryKey, scope, plan, sort, page, uploadActive }: GridQuery) {
  const [reloadKey, setReloadKey] = useState(0)
  // Set by the background refresh during a job: that fetch keeps the cards on screen (#286).
  const silentReload = useRef(false)
  // Set when rows may have joined or left the filtered set, so the next fetch counts again.
  const recount = useRef(false)
  const countedFor = useRef<string | null>(null)
  const [media, setMedia] = useState<MediaRecord[]>([])
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Media whose thumbnail failed to load, shown as "processing" (the rendition is likely still
  // generating). Reset on every (re)load so they re-attempt.
  const [failedThumbs, setFailedThumbs] = useState<ReadonlySet<string>>(new Set())
  // When the current page of media arrived: the "now" for deciding a missing thumbnail is
  // stuck rather than still on its way (#208), kept out of render so it stays pure.
  const [loadedAt, setLoadedAt] = useState(0)

  /** Read the page again; `countAgain` after a write that may move rows in or out of the filter. */
  const reload = useCallback((countAgain: boolean) => {
    if (countAgain) recount.current = true
    setReloadKey(k => k + 1)
  }, [])
  /** Read the page again keeping the cards on screen, for the refresh while a job runs. */
  const refreshQuietly = useCallback(() => {
    silentReload.current = true
    setReloadKey(k => k + 1)
  }, [])
  const markThumbFailed = useCallback((id: string) => setFailedThumbs(s => new Set(s).add(id)), [])

  useEffect(() => {
    const silent = silentReload.current
    silentReload.current = false
    if (!user) return

    if (deploymentIds.length === 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- nothing to read: the page empties at once
      setMedia([])
      setTotalCount(0)
      return
    }

    let cancelled = false
    if (!silent) setLoading(true)
    setError(null)

    const from = page * PAGE_SIZE
    const to = from + PAGE_SIZE - 1
    const wantCount = countedFor.current !== queryKey || recount.current
    const order = gridSortPlan(sort)

    const query = supabase
      .from('media')
      .select([MEDIA_SELECT, ...scope.embeds, ...plan.embeds, ...order.embeds].join(', '), wantCount ? { count: 'exact' } : undefined)
      .is('deleted_at', null)
    applyOrder(applyFilterOps(query, [...scope.ops, ...plan.ops]), order.orders)
      .range(from, to)
      .then(({ data, error: err, count }) => {
        if (cancelled) return
        if (err) { setError(err.message); setLoading(false); return }
        setMedia((data || []) as unknown as MediaRecord[])
        if (wantCount) {
          countedFor.current = queryKey
          recount.current = false
          setTotalCount(count ?? null)
        }
        setFailedThumbs(new Set())  // re-attempt thumbnails (renditions may now exist)
        setLoadedAt(Date.now())
        setLoading(false)
      })

    return () => { cancelled = true }
  }, [user, deploymentIds, queryKey, scope, plan, sort, page, reloadKey])

  // While an upload is running, poll so newly-registered media appear in the grid
  // without a manual refresh, and do one final refresh when it finishes (cleanup
  // runs on the isActive → false transition, catching the last batch), counted again.
  useEffect(() => {
    if (!uploadActive) return
    const t = setInterval(() => setReloadKey(k => k + 1), 4000)
    return () => { clearInterval(t); reload(true) }
  }, [uploadActive, reload])

  return {
    media, setMedia, totalCount, setTotalCount, loading, error, setError,
    failedThumbs, markThumbFailed, loadedAt, reload, refreshQuietly,
  }
}
