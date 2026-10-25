// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useHasRealtimeData: drives the conditional Realtime nav tab (UI audit decision 7). The tab shows
// only when a device in one of the user's projects sends or has sent LoRaWAN data, meaning at
// least one lorawan_messages row the user can read. RLS lets a user read a message through its
// deployment's project (has_project_role, project_viewer), so one limit-1 read answers it.
//
// It asks about every project the user can read, not the header selection, so the tab does not
// come and go as projects are ticked. The last answer seeds the next page load, so a returning
// user sees the tab at once; the read then confirms or removes it.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from './useAuth'
import { useProjectSelection } from './useProjectSelection'
import { supabase } from '../config/supabase'

const LS_KEY = 'ww:hasRealtimeData'
// The key this hook replaced (open deployments, not LoRaWAN data); cleared once.
const OLD_LS_KEY = 'ww:hasActiveDeployments'

// Above this many projects the id list would make a long URL; RLS alone then scopes the read.
const MAX_FILTER_IDS = 100

function readSeed(): boolean | undefined {
  try { return localStorage.getItem(LS_KEY) === '1' ? true : undefined } catch { return undefined }
}

export function useHasRealtimeData(): boolean {
  const { user } = useAuth()
  const { projects, isLoading } = useProjectSelection()
  const ids = projects.map(p => p.id).sort()

  const { data } = useQuery<boolean>({
    queryKey: ['hasRealtimeData', user?.id, ids],
    enabled: !!user && !isLoading && ids.length > 0,
    staleTime: 5 * 60 * 1000,
    placeholderData: readSeed,
    queryFn: async () => {
      let query = supabase.from('lorawan_messages').select('id, deployments!inner(project_id)').limit(1)
      if (ids.length <= MAX_FILTER_IDS) query = query.in('deployments.project_id', ids)
      const { data: rows, error } = await query
      const has = !error && (rows?.length ?? 0) > 0
      try {
        localStorage.setItem(LS_KEY, has ? '1' : '0')
        localStorage.removeItem(OLD_LS_KEY)
      } catch { /* private mode */ }
      return has
    },
  })

  // No projects, no devices: the seed must not keep the tab for a user who left every project.
  return (isLoading || ids.length > 0) && !!data
}
