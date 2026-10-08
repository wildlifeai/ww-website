// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Click and key rules for the Review grid cards (#283). A plain click or Enter opens the viewer;
// the selection circle, Ctrl/Cmd-click and Space toggle a card; Shift selects a range. The rule
// does not change while a selection is active, so a plain click always opens.

/** What a gesture on a card does. */
export type CardIntent = 'open' | 'toggle' | 'range'

interface Modifiers {
  ctrlKey: boolean
  metaKey: boolean
  shiftKey: boolean
}

/** A click on the card body: Shift selects a range, Ctrl/Cmd toggles, a plain click opens. */
export function cardClickIntent(e: Modifiers): CardIntent {
  if (e.shiftKey) return 'range'
  if (e.ctrlKey || e.metaKey) return 'toggle'
  return 'open'
}

/** A click on the selection circle never opens: Shift selects a range, anything else toggles. */
export function circleClickIntent(e: Pick<Modifiers, 'shiftKey'>): CardIntent {
  return e.shiftKey ? 'range' : 'toggle'
}

/**
 * A key pressed on a focused card: Enter opens, Space toggles, anything else is left alone
 * (null). A key aimed at a control inside the card, such as the circle, belongs to that control.
 */
export function cardKeyIntent(e: { key: string; target: unknown; currentTarget: unknown }): CardIntent | null {
  if (e.target !== e.currentTarget) return null
  if (e.key === 'Enter') return 'open'
  if (e.key === ' ') return 'toggle'
  return null
}

export interface CardSelection {
  selected: ReadonlySet<string>
  /** The last card selected, where a Shift range starts. */
  anchor: string | null
}

/**
 * The selection after a toggle or range gesture on card `id`. `order` is the grid's card order
 * (one entry per media, as rendered). A range adds every card from the anchor to `id`, inclusive,
 * and never deselects; with no selected anchor in the grid it just selects `id`.
 */
export function applySelectIntent(
  state: CardSelection,
  intent: 'toggle' | 'range',
  id: string,
  order: readonly string[],
): { selected: Set<string>; anchor: string | null } {
  const selected = new Set(state.selected)
  if (intent === 'toggle') {
    if (selected.delete(id)) return { selected, anchor: state.anchor === id ? null : state.anchor }
    selected.add(id)
    return { selected, anchor: id }
  }
  const from = state.anchor && selected.has(state.anchor) ? order.indexOf(state.anchor) : -1
  const to = order.indexOf(id)
  if (from < 0 || to < 0) {
    selected.add(id)
    return { selected, anchor: id }
  }
  for (const card of order.slice(Math.min(from, to), Math.max(from, to) + 1)) selected.add(card)
  return { selected, anchor: id }
}
