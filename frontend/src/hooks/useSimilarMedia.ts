// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useSimilarMedia: the review grid's "Find similar" mode. The embedding nearest neighbours of an
// anchor photo, read as full media records (so they label and select like any grid card) and
// kept in similarity order, the anchor first.
import { useEffect, useState } from 'react'
import { supabase } from '../config/supabase'
import { fetchMediaInOrder, type MediaRecord } from '../lib/gridMedia'
import { useSimilarImages } from './useBrain'

/** Neighbours asked for; the anchor leads them. */
const NEIGHBOURS = 30

/** `onError` must be stable (a state setter): it is a dependency of the read. */
export function useSimilarMedia(onError: (message: string) => void) {
  const [similarToId, setSimilarToId] = useState<string | null>(null)
  const [records, setRecords] = useState<MediaRecord[]>([])
  const [loading, setLoading] = useState(false)
  const similarQ = useSimilarImages(similarToId, NEIGHBOURS)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- leaving the mode empties the list at once
    if (!similarToId) { setRecords([]); return }
    const hits = similarQ.data?.results
    if (!hits) return
    let cancelled = false
    setLoading(true)
    fetchMediaInOrder(supabase, [similarToId, ...hits.map(h => h.media_id)])
      .then(recs => { if (!cancelled) setRecords(recs) })
      .catch(e => { if (!cancelled) onError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [similarToId, similarQ.data, onError])

  return { similarToId, setSimilarToId, records, setRecords, loading, failed: similarQ.isError }
}
