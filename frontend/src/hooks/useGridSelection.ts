// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridSelection: the photos selected on the review grid (#283, rules in lib/cardSelection).
// A selection belongs to one page of one query: a new page, filter, order or the similar list
// starts with nothing selected, so "All on page" means what is on screen. Ctrl/Cmd+A selects the
// page once a selection has started, and Escape clears it while the viewer is closed.
import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { applySelectIntent } from '../lib/cardSelection'

const NONE: ReadonlySet<string> = new Set()

/**
 * `pageKey` names the page; `selectableIds` are its photos the user may select, in order;
 * `viewerOpen` leaves Escape to the viewer.
 */
export function useGridSelection(pageKey: string, selectableIds: readonly string[], viewerOpen: boolean) {
  const [selection, setSelection] = useState<{ key: string; ids: ReadonlySet<string> }>({ key: '', ids: NONE })
  const selectedIds = selection.key === pageKey ? selection.ids : NONE
  const setSelectedIds = (ids: ReadonlySet<string>) => setSelection({ key: pageKey, ids })
  const clearSelection = useCallback(() => setSelection({ key: '', ids: NONE }), [])
  // The last card selected, where a Shift-click range starts.
  const anchor = useRef<string | null>(null)

  const selectAllOnPage = () => setSelectedIds(new Set(selectableIds))
  /** A toggle or range gesture on a card; `order` is the grid's selectable photos as shown. */
  const select = (id: string, intent: 'toggle' | 'range', order: readonly string[]) => {
    const next = applySelectIntent({ selected: selectedIds, anchor: anchor.current }, intent, id, order)
    anchor.current = next.anchor
    setSelectedIds(next.selected)
  }
  /** A group's Select or Deselect. */
  const toggleGroup = (ids: readonly string[], allSelected: boolean) => {
    const next = new Set(selectedIds)
    for (const id of ids) {
      if (allSelected) next.delete(id)
      else next.add(id)
    }
    setSelectedIds(next)
  }

  const onWindowKey = useEffectEvent((e: KeyboardEvent) => {
    // Ctrl/Cmd+A: select every selectable photo on the page (once a selection has started)
    if ((e.ctrlKey || e.metaKey) && e.key === 'a' && selectedIds.size > 0) {
      e.preventDefault()
      selectAllOnPage()
    }
    // Escape: clear the selection. While the viewer is open its own Escape closes it, and the
    // selection survives.
    if (e.key === 'Escape' && !viewerOpen && selectedIds.size > 0) clearSelection()
  })
  useEffect(() => {
    const handler = (e: KeyboardEvent) => onWindowKey(e)
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return { selectedIds, clearSelection, selectAllOnPage, select, toggleGroup }
}
