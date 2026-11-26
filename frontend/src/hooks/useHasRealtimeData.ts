// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useHasRealtimeData: whether the Realtime nav tab opens (otherwise it shows greyed out,
// RealtimeTabOff). It opens when the projects selected in the header have a camera the Realtime
// page shows: deployed now or collected today, with LoRaWAN on or a message sent
// (hasLorawanCameras in lib/realtimeReads.ts, the page's own rule). It follows the selection, so
// a project with no LoRaWAN camera greys the tab (Victor, 27 Nov 2026). RLS scopes both reads to
// the projects the user can read.
//
// The last answer seeds the next page load, so a returning user finds the tab open at once; the
// read then confirms or greys it.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from './useAuth'
import { useProjectSelection } from './useProjectSelection'
import { supabase } from '../config/supabase'
import { hasLorawanCameras } from '../lib/realtimeReads'

const LS_KEY = 'ww:hasRealtimeData'
// The key this hook replaced (open deployments, not LoRaWAN data); cleared once.
const OLD_LS_KEY = 'ww:hasActiveDeployments'

function readSeed(): boolean | undefined {
  try { return localStorage.getItem(LS_KEY) === '1' ? true : undefined } catch { return undefined }
}

export function useHasRealtimeData(): boolean {
  const { user } = useAuth()
  const { queryProjectIds, isLoading } = useProjectSelection()
  const ids = (queryProjectIds ?? []).toSorted()

  const { data } = useQuery<boolean>({
    queryKey: ['hasRealtimeData', user?.id, ids],
    enabled: !!user && ids.length > 0,
    staleTime: 5 * 60 * 1000,
    placeholderData: readSeed,
    queryFn: async () => {
      const has = await hasLorawanCameras(supabase, ids)
      try {
        localStorage.setItem(LS_KEY, has ? '1' : '0')
        localStorage.removeItem(OLD_LS_KEY)
      } catch { /* private mode */ }
      return has
    },
  })

  // Nothing selected, nothing to show: the seed must not open the tab then.
  return (isLoading || ids.length > 0) && !!data
}
