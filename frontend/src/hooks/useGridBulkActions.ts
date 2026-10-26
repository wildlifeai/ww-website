// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridBulkActions: what the review grid's Actions menu does with the selected photos (label,
// find similar, publish to iNaturalist, remove, run AI, train) and which of its dialogs is open.
// The grid only lets people select photos in projects they can write to (decision 1).
import { useState, type Dispatch, type SetStateAction } from 'react'
import type { User } from '@supabase/supabase-js'
import type { BulkAction } from '../components/data/MediaBulkActions'
import type { SpeciesSelection } from '../components/data/SpeciesPicker'
import { showErrorToast, showUndoToast } from '../components/common/undoToastBus'
import { supabase } from '../config/supabase'
import { apiClient } from '../lib/apiClient'
import type { MediaRecord } from '../lib/gridMedia'
import { writeVerdicts, type VerdictLabel } from '../lib/humanVerdict'
import { mediaDeleteOutcome, type MediaDeleteResponse } from '../lib/mediaDelete'

export type BulkDialog = 'delete' | 'ai' | 'label' | 'train'

interface Args {
  user: User | null
  selectedIds: ReadonlySet<string>
  /** The cards on screen: the page, or the similar list. */
  visible: readonly MediaRecord[]
  /** The page's own rows, which a delete's Undo puts back. */
  media: readonly MediaRecord[]
  isSelectable: (m: MediaRecord) => boolean
  clearSelection: () => void
  reload: (countAgain: boolean) => void
  setMedia: Dispatch<SetStateAction<MediaRecord[]>>
  setSimilar: Dispatch<SetStateAction<MediaRecord[]>>
  setTotalCount: Dispatch<SetStateAction<number | null>>
  onFindSimilar: (anchorId: string) => void
  onPublish: (ids: string[]) => void
}

export function useGridBulkActions(a: Args) {
  const [dialog, setDialog] = useState<BulkDialog | null>(null)
  const [labelBusy, setLabelBusy] = useState(false)
  const [pipelineLogs, setPipelineLogs] = useState<string[] | null>(null)

  const onAction = (action: BulkAction) => {
    if (action === 'similar') {
      // Anchor on the single selected image and enter similarity-ranked mode.
      const anchor = [...a.selectedIds][0]
      if (anchor) { a.onFindSimilar(anchor); a.clearSelection() }
    } else if (action === 'inat') {
      // Not connected, the menu links to the Toolkit page instead of offering this.
      a.onPublish([...a.selectedIds])
    } else {
      setDialog(action)
    }
  }

  const deleteSelected = async (ids: string[]) => {
    const res = await apiClient.del('/api/media/batch', { media_ids: ids }) as { data?: MediaDeleteResponse }
    const { deletedIds, deletedAt, message } = mediaDeleteOutcome(ids, res?.data)
    // Nothing deleted: throw so the confirm modal stays open and shows why.
    if (deletedIds.length === 0) throw new Error(message)
    const gone = new Set(deletedIds)
    // Capture the rows so Undo can re-insert them without a full refetch.
    const removed = a.media.filter(m => gone.has(m.id))
    a.setMedia(prev => prev.filter(m => !gone.has(m.id)))
    a.setSimilar(prev => prev.filter(m => !gone.has(m.id)))
    a.setTotalCount(t => (t === null ? t : Math.max(0, t - deletedIds.length)))
    a.clearSelection()
    if (deletedAt) {
      showUndoToast({
        message,
        onUndo: async () => {
          await apiClient.post('/api/media/batch/restore', { media_ids: deletedIds, deleted_at: deletedAt })
          a.setMedia(prev => {
            const have = new Set(prev.map(m => m.id))
            return [...removed.filter(m => !have.has(m.id)), ...prev]
          })
          a.setTotalCount(t => (t === null ? t : t + deletedIds.length))
        },
      })
    }
  }

  const runAi = async (models: string[]) => {
    const ids = Array.from(a.selectedIds)
    setPipelineLogs([`Starting AI pipeline for ${ids.length} images with models: ${models.join(', ')}…`])
    try {
      // Through apiClient: the API's own origin and the session token. A bare relative fetch went
      // to the site, not the API, and carried no token.
      const res = await apiClient.post('/api/media/run-selected', { media_ids: ids, steps: models }) as {
        data?: { job_id?: string }
      }
      setPipelineLogs(prev => [...(prev || []), `Job queued: ${res.data?.job_id ?? 'unknown'}`])
    } catch (e) {
      setPipelineLogs(prev => [...(prev || []), `Error: ${e instanceof Error ? e.message : 'unknown'}`])
    }
  }

  // Bulk "Label as": one human verdict per selected photo, updated when the photo has one, and
  // AI rows untouched (lib/humanVerdict, decision 2), with Undo; a refusal shows in the same toast.
  // iNat community-ID sync lives in Settings.
  const label = async (choice: SpeciesSelection | null) => {
    if (labelBusy) return
    const photos = a.visible.filter(m => a.selectedIds.has(m.id) && a.isSelectable(m))
    if (photos.length === 0) { setDialog(null); return }
    setLabelBusy(true)
    const verdict: VerdictLabel = choice
      ? { observation_type: 'animal', taxon_id: choice.taxon_id, scientific_name: choice.scientific_name, vernacular_name: choice.vernacular_name }
      : { observation_type: 'blank' }
    try {
      const result = await writeVerdicts(supabase, photos, verdict, { userId: a.user?.id, userEmail: a.user?.email })
      // The cards show the new verdicts at once; the reload then counts again, since a status
      // filter may no longer match them.
      const written = (m: MediaRecord) => {
        const rows = result.observations.get(m.id)
        return rows ? { ...m, observations: rows } : m
      }
      a.setMedia(prev => prev.map(written))
      a.setSimilar(prev => prev.map(written))
      a.clearSelection()
      a.reload(true)
      const name = choice ? (choice.vernacular_name || choice.scientific_name) : 'empty'
      showUndoToast({
        message: `Labelled ${result.saved} photo${result.saved !== 1 ? 's' : ''} as ${name}`,
        onUndo: async () => {
          await result.undo()
          a.reload(true)
        },
      })
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : String(e))
    } finally {
      setLabelBusy(false)
      setDialog(null)
    }
  }

  return {
    dialog, closeDialog: () => setDialog(null), onAction, deleteSelected, runAi, label, labelBusy,
    pipelineLogs, closeLogs: () => setPipelineLogs(null),
  }
}

export type GridBulkActions = ReturnType<typeof useGridBulkActions>
