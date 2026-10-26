// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridViewer: which photo the review grid's full-screen viewer shows, stepping through the
// cards on screen. A linked photo (?media=, from the UMAP explorer) opens even when it is not one
// of them.
import { useEffect, useState } from 'react'
import { supabase } from '../config/supabase'
import { fetchMediaInOrder, type MediaRecord } from '../lib/gridMedia'

export function useGridViewer(visible: readonly MediaRecord[], initialMediaId: string | undefined, userId: string | undefined) {
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)
  // The observation a crop card was opened from, pre-selected in the detail.
  const [focusObsId, setFocusObsId] = useState<string | null>(null)
  const [linkedMedia, setLinkedMedia] = useState<MediaRecord | null>(null)

  useEffect(() => {
    if (!initialMediaId || !userId) return
    let cancelled = false
    fetchMediaInOrder(supabase, [initialMediaId])
      .then(([rec]) => { if (!cancelled && rec) { setLinkedMedia(rec); setSelectedMediaId(rec.id) } })
      .catch(() => { /* a photo that cannot be read just does not open */ })
    return () => { cancelled = true }
  }, [initialMediaId, userId])

  const selectedMedia = visible.find(m => m.id === selectedMediaId)
    ?? (linkedMedia?.id === selectedMediaId ? linkedMedia : null)

  const open = (id: string, focusObs: string | null) => {
    setFocusObsId(focusObs)
    setSelectedMediaId(id)
  }
  // AN-4: after a one-click Confirm/Blank, jump to the next image in the grid.
  const next = () => {
    if (!selectedMediaId) return
    const idx = visible.findIndex(m => m.id === selectedMediaId)
    const following = idx >= 0 && idx < visible.length - 1 ? visible[idx + 1] : null
    setSelectedMediaId(following ? following.id : null)
  }
  // Step back to the previous image in the grid (modal ‹ arrow).
  const prev = () => {
    if (!selectedMediaId) return
    const idx = visible.findIndex(m => m.id === selectedMediaId)
    if (idx > 0) setSelectedMediaId(visible[idx - 1].id)
  }
  /** Keep the linked photo in step with an edit made in the viewer. */
  const updateLinked = (updated: MediaRecord) => setLinkedMedia(m => (m?.id === updated.id ? updated : m))

  return { selectedMediaId, setSelectedMediaId, focusObsId, selectedMedia, open, next, prev, updateLinked }
}
