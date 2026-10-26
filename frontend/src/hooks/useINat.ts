/* eslint-disable react-hooks/set-state-in-effect */
import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../lib/apiClient'
import { isInatOff } from '../lib/inatOff'
import { publishInRounds, type INatPublishResult } from '../lib/inatPublishRounds'

export type { INatPublishResult }

/**
 * useINat — iNaturalist connection status + publish action for the web app.
 *
 * - `connected` / `username`: from GET /api/inat/status
 * - `enabled`: false when FF_INAT_ENABLED is off (the status route answers 404 "not enabled")
 * - `connect()`: starts the OAuth flow (redirects to iNaturalist)
 * - `publish(mediaIds, opts)`: POST /api/inat/publish (burst-consolidated upload), in rounds
 *   until every photo is reached (lib/inatPublishRounds.ts), answering once for them all
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function unwrap(r: any) { return r?.data ?? r }

export function useINat() {
  const [connected, setConnected] = useState<boolean | null>(null)
  const [username, setUsername] = useState<string | null>(null)
  const [enabled, setEnabled] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const d = unwrap(await apiClient.get('/api/inat/status'))
      setConnected(!!d.connected)
      setUsername(d.inat_username ?? null)
    } catch (e) {
      if (isInatOff(e)) setEnabled(false)
      setConnected(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const connect = useCallback(async () => {
    const d = unwrap(await apiClient.get('/api/inat/auth'))
    if (d.authorization_url) window.location.href = d.authorization_url
  }, [])

  // Pathway 2: connect with a personal API token the user pastes from
  // https://www.inaturalist.org/users/api_token (no OAuth app / callback needed).
  const setToken = useCallback(async (apiToken: string) => {
    const d = unwrap(await apiClient.post('/api/inat/token', { api_token: apiToken.trim() }))
    setConnected(!!d.connected)
    setUsername(d.inat_username ?? null)
  }, [])

  const disconnect = useCallback(async () => {
    await apiClient.post('/api/inat/disconnect')
    setConnected(false)
    setUsername(null)
  }, [])

  const publish = useCallback(async (
    mediaIds: string[],
    opts?: { gap_seconds?: number; geoprivacy?: string },
  ): Promise<INatPublishResult> => {
    return publishInRounds(async ids => unwrap(await apiClient.post('/api/inat/publish', {
      media_ids: ids,
      gap_seconds: opts?.gap_seconds ?? 60,
      geoprivacy: opts?.geoprivacy ?? 'obscured',
    })), mediaIds)
  }, [])

  // Pull community identifications from iNat back into WW (updates badges).
  const sync = useCallback(async (): Promise<{
    checked: number; updated: number; research: number; disagreement: number; observations_written: number
  }> => {
    return unwrap(await apiClient.post('/api/inat/sync'))
  }, [])

  return { connected, username, enabled, connect, setToken, disconnect, publish, sync, refresh }
}
