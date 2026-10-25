// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useNotifications, the in-app notification feed (avatar badge + dropdown + page).
// Reads the RLS-scoped `notifications` table directly (each row belongs to the signed-in
// user). Polls every 45s + on window focus. Degrades gracefully when the table isn't
// deployed yet (the ww-backend migration may not have reached this environment): any
// query error is treated as "no notifications / unavailable" so the UI never breaks.
//
// The type and unread filters run in the query, the list grows a page at a time ("Load more"),
// and the unread badge is a server-side count, not a count of the loaded rows (UI audit 01 E1).
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from './useAuth'
import { supabase } from '../config/supabase'

export interface AppNotification {
  id: string
  created_at: string
  project_id: string | null
  deployment_id: string | null
  type: string
  title: string
  body: string | null
  data: Record<string, unknown> | null
  read_at: string | null
}

/** Rows per "Load more". */
export const NOTIFICATIONS_PAGE = 50

export interface NotificationFilter {
  /** One of the table's types (`species_detection`, `camera_silent`, `upload_complete`, `system`), or all. */
  type?: string
  unreadOnly?: boolean
}

const POLL = { refetchInterval: 45_000, refetchOnWindowFocus: true } as const

/**
 * The newest `limit` notifications matching `filter`, whether more exist, and the user's unread
 * count. Called with no arguments by the avatar menu, which shows the badge and the latest five.
 */
export function useNotifications(filter: NotificationFilter = {}, limit = NOTIFICATIONS_PAGE) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const type = filter.type ?? ''
  const unreadOnly = !!filter.unreadOnly

  const list = useQuery({
    queryKey: ['notifications', user?.id, 'list', type, unreadOnly, limit],
    enabled: !!user,
    ...POLL,
    // Keep the rows on screen while a filter change or "Load more" reads the next set.
    placeholderData: keepPreviousData,
    queryFn: async () => {
      let q = supabase
        .from('notifications')
        .select('id, created_at, project_id, deployment_id, type, title, body, data, read_at')
      if (type) q = q.eq('type', type)
      if (unreadOnly) q = q.is('read_at', null)
      // One row past the page says whether there is more to load.
      const { data, error } = await q
        .order('created_at', { ascending: false })
        .order('id', { ascending: false })
        .range(0, limit)
      // Table not deployed yet / no access → treat as empty + unavailable.
      if (error) return { items: [] as AppNotification[], available: false, hasMore: false }
      const rows = (data ?? []) as AppNotification[]
      return { items: rows.slice(0, limit), available: true, hasMore: rows.length > limit }
    },
  })

  const unread = useQuery({
    queryKey: ['notifications', user?.id, 'unread'],
    enabled: !!user,
    ...POLL,
    queryFn: async () => {
      const { count, error } = await supabase
        .from('notifications')
        .select('id', { count: 'exact', head: true })
        .is('read_at', null)
      return error ? 0 : count ?? 0
    },
  })

  const items = list.data?.items ?? []
  const available = list.data?.available ?? false
  const hasMore = list.data?.hasMore ?? false
  const unreadCount = unread.data ?? 0

  const invalidate = () => qc.invalidateQueries({ queryKey: ['notifications', user?.id] })

  const markRead = async (id: string) => {
    await supabase.from('notifications').update({ read_at: new Date().toISOString() }).eq('id', id).is('read_at', null)
    await invalidate()
  }

  const markAllRead = async () => {
    await supabase.from('notifications').update({ read_at: new Date().toISOString() }).is('read_at', null)
    await invalidate()
  }

  return {
    items,
    unreadCount,
    available,
    hasMore,
    loading: list.isLoading,
    /** True while a filter change or "Load more" is reading, with the previous rows still shown. */
    fetching: list.isPlaceholderData,
    markRead,
    markAllRead,
    refresh: list.refetch,
  }
}
