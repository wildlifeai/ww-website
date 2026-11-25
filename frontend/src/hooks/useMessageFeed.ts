// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useMessageFeed: the Realtime page's Messages tab (lib/lorawanFeed.ts). One cached entry per set
// of shown deployments holds the rows; a read of the newest page (on open, and each catch-up the
// page asks for) merges into it, "Load older" adds the page before the oldest row, and a live
// message is merged in by useFieldLive.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { realtimeKeys } from '../lib/realtimeReads'
import {
  fetchFeedPage, olderLimit, withNewestPage, withOlderPage, type FeedState,
} from '../lib/lorawanFeed'

export function useMessageFeed(deploymentIds: string[]) {
  const queryClient = useQueryClient()
  const key = realtimeKeys.feed(deploymentIds)
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      const page = await fetchFeedPage(supabase, deploymentIds)
      // The cache after the read, so a live row merged meanwhile is kept.
      return withNewestPage(queryClient.getQueryData<FeedState>(key), page)
    },
    enabled: deploymentIds.length > 0,
  })
  const [older, setOlder] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null })

  const state = query.data
  const canLoadOlder = !!state && state.hasOlder && olderLimit(state) > 0

  const loadOlder = async () => {
    if (!state || !canLoadOlder || older.loading) return
    setOlder({ loading: true, error: null })
    let error: string | null = null
    try {
      const page = await fetchFeedPage(supabase, deploymentIds, { before: state.rows.at(-1), limit: olderLimit(state) })
      queryClient.setQueryData<FeedState>(key, s => s && withOlderPage(s, page))
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      setOlder({ loading: false, error })
    }
  }

  return {
    state,
    loading: query.isPending && deploymentIds.length > 0,
    error: query.error?.message ?? null,
    canLoadOlder,
    loadOlder,
    loadingOlder: older.loading,
    olderError: older.error,
  }
}
