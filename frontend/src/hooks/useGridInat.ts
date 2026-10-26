// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridInat: the review grid's iNaturalist badges for the photos on the page, and publishing a
// selection as Cluster Review does: POST /api/inat/publish (burst-consolidated) through useINat,
// with the blocker and notices of lib/inatPublish.
import { useCallback, useEffect, useState } from 'react'
import type { BannerTone } from '../components/ui/Banner'
import type { INatState } from '../components/data/INatBadge'
import { supabase } from '../config/supabase'
import type { MediaRecord } from '../lib/gridMedia'
import { inatBlocker, inatErrorNotice, inatPublishNotice, type CardNotice } from '../lib/inatPublish'
import { useINat } from './useINat'

export type InatStates = ReadonlyMap<string, { state: INatState; uri: string | null }>

interface Args {
  /** The photos on the page; their badges load again whenever the list changes. */
  media: readonly MediaRecord[]
  flash: (tone: BannerTone, text: string, autoHide?: boolean) => void
  showCardNotice: (n: CardNotice) => void
  /** Called once the route answered, as Cluster Review clears its selection. */
  onPublished: () => void
}

export function useGridInat({ media, flash, showCardNotice, onPublished }: Args) {
  const inat = useINat()
  const [inatStates, setInatStates] = useState<InatStates>(new Map())
  const [busy, setBusy] = useState(false)

  // Load iNat upload/sync state for a set of media (graceful if table absent).
  const loadInatStates = useCallback(async (ids: string[]) => {
    if (ids.length === 0) { setInatStates(new Map()); return }
    const { data, error: err } = await supabase
      .from('inat_observation_media')
      .select('media_id, inat_observations(sync_status, inat_uri)')
      .in('media_id', ids)
    if (err || !data) return  // table not migrated yet / no access → no badges
    const map = new Map<string, { state: INatState; uri: string | null }>()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    for (const row of data as any[]) {
      const io = Array.isArray(row.inat_observations) ? row.inat_observations[0] : row.inat_observations
      if (io?.sync_status) map.set(row.media_id, { state: io.sync_status as INatState, uri: io.inat_uri ?? null })
    }
    setInatStates(map)
  }, [])

  // Refresh iNaturalist badges whenever the loaded media set changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect -- an empty page clears the badges at once
  useEffect(() => { loadInatStates(media.map(m => m.id)) }, [media, loadInatStates])

  const publish = async (ids: string[]) => {
    if (ids.length === 0 || busy) return
    const blocker = inatBlocker({ connected: inat.connected, selected: ids.length })
    if (blocker) { showCardNotice(blocker); return }
    setBusy(true)
    flash('info', 'Publishing to iNaturalist…', false)
    try {
      showCardNotice(inatPublishNotice(await inat.publish(ids)))
      onPublished()
      await loadInatStates(media.map(m => m.id))
    } catch (e) {
      showCardNotice(inatErrorNotice(e))
    } finally {
      setBusy(false)
    }
  }

  return { inat, inatStates, publish }
}
