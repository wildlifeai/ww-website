// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridSecondLook: the review grid's "Needs a second look" (#448, decisions 7 to 9 and 14). The
// open label-check flags of the projects in view, above the sensitivity slider's score: a badge on
// each flagged photo of the page, and a mode that lists the flagged photos in review order (or one
// confused pair's), each with its reason and "Checked, it's right". A photo whose label changed
// since it was flagged loses its flag at once (lib/labelChecks); one relabelled in the viewer stays
// listed, without its reason, until the mode is left, so the viewer keeps its place.
import { useEffect, useMemo, useState } from 'react'
import { supabase } from '../config/supabase'
import { fetchMediaInOrder, type MediaRecord } from '../lib/gridMedia'
import {
  DEFAULT_SENSITIVITY, confusedPairs, inPair, minScore, orderChecks, stillLabelled, strongestByMedia, type LabelCheck,
} from '../lib/labelChecks'
import { photoVerdict } from '../lib/observations'
import { useLabelChecks, useMarkChecked } from './useLabelChecks'

/** Flagged photos the mode lists at once; checking or relabelling them makes room for the next. */
export const SECOND_LOOK_MAX = 100

interface Args {
  projectIds: readonly string[]
  enabled: boolean
  /** `onError` must be stable (a state setter): it is a dependency of the read. */
  onError: (message: string) => void
}

export function useGridSecondLook({ projectIds, enabled, onError }: Args) {
  const checksQ = useLabelChecks(projectIds, enabled)
  const mark = useMarkChecked()
  const [on, setOn] = useState(false)
  const [sensitivity, setSensitivity] = useState(DEFAULT_SENSITIVITY)
  const [pair, setPair] = useState<[string, string] | null>(null)
  const [cache, setCache] = useState<ReadonlyMap<string, MediaRecord>>(new Map())
  const [relabelled, setRelabelled] = useState<ReadonlySet<string>>(new Set())
  const [loading, setLoading] = useState(false)

  const floor = minScore(sensitivity)
  const checks = useMemo(() => (checksQ.data ?? []).filter(c => c.score >= floor), [checksQ.data, floor])
  const pairs = useMemo(() => confusedPairs(checks), [checks])
  const byMedia = useMemo(() => strongestByMedia(checks), [checks])
  const listed = useMemo(() => {
    const ordered = orderChecks(pair ? checks.filter(c => inPair(c, pair)) : checks)
    return [...new Set(ordered.map(c => c.media_id))].slice(0, SECOND_LOOK_MAX)
  }, [checks, pair])

  // The listed photos' records, read once each.
  const missingKey = on ? listed.filter(id => !cache.has(id)).join(',') : ''
  useEffect(() => {
    if (!missingKey) return
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the read below is what it waits for
    setLoading(true)
    fetchMediaInOrder(supabase, missingKey.split(','))
      .then(recs => { if (!cancelled) setCache(prev => new Map([...prev, ...recs.map(r => [r.id, r] as const)])) })
      .catch(e => { if (!cancelled) onError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [missingKey, onError])

  /** The photo's flag, while its label is still the one flagged. */
  const flagOf = (m: MediaRecord): LabelCheck | undefined => {
    const c = byMedia.get(m.id)
    return c && stillLabelled(c, photoVerdict(m.observations).labelObs?.scientific_name) ? c : undefined
  }
  const records = on ? listed.flatMap(id => {
    const m = cache.get(id)
    return m && (flagOf(m) || relabelled.has(id)) ? [m] : []
  }) : []

  return {
    available: enabled && checksQ.isSuccess,
    on,
    setOn: (value: boolean) => { setOn(value); setPair(null); setRelabelled(new Set()) },
    count: byMedia.size,
    sensitivity, setSensitivity,
    pairs, pair, setPair,
    records, loading: loading || checksQ.isLoading,
    flagOf,
    markChecked: (checkId: string) => mark.mutate(checkId, { onError: e => onError(e instanceof Error ? e.message : String(e)) }),
    marking: mark.isPending,
    /** A record the viewer changed, so its card shows the new label (and drops out once relabelled). */
    updateRecord: (updated: MediaRecord) => {
      if (!cache.has(updated.id)) return
      setCache(prev => new Map(prev).set(updated.id, updated))
      if (on && !flagOf(updated)) setRelabelled(prev => new Set(prev).add(updated.id))
    },
  }
}

export type GridSecondLook = ReturnType<typeof useGridSecondLook>
