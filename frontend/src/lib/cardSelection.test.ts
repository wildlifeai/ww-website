import { describe, expect, it } from 'vitest'
import { applySelectIntent, cardClickIntent, cardKeyIntent, circleClickIntent } from './cardSelection'

const click = (mods: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }> = {}) =>
  ({ ctrlKey: false, metaKey: false, shiftKey: false, ...mods })
const ORDER = ['a', 'b', 'c', 'd', 'e']

describe('cardClickIntent', () => {
  it('opens on a plain click', () => {
    expect(cardClickIntent(click())).toBe('open')
  })

  it('toggles on Ctrl-click and Cmd-click', () => {
    expect(cardClickIntent(click({ ctrlKey: true }))).toBe('toggle')
    expect(cardClickIntent(click({ metaKey: true }))).toBe('toggle')
  })

  it('selects a range on Shift-click, with or without Ctrl', () => {
    expect(cardClickIntent(click({ shiftKey: true }))).toBe('range')
    expect(cardClickIntent(click({ shiftKey: true, ctrlKey: true }))).toBe('range')
  })
})

describe('circleClickIntent', () => {
  it('toggles and never opens', () => {
    expect(circleClickIntent(click())).toBe('toggle')
    expect(circleClickIntent(click({ ctrlKey: true }))).toBe('toggle')
  })

  it('selects a range on Shift-click', () => {
    expect(circleClickIntent(click({ shiftKey: true }))).toBe('range')
  })
})

describe('cardKeyIntent', () => {
  const card = {}
  const key = (k: string, target: unknown = card) => cardKeyIntent({ key: k, target, currentTarget: card })

  it('opens on Enter and toggles on Space', () => {
    expect(key('Enter')).toBe('open')
    expect(key(' ')).toBe('toggle')
  })

  it('leaves other keys alone', () => {
    expect(key('a')).toBeNull()
    expect(key('Escape')).toBeNull()
  })

  it('leaves keys aimed at the circle inside the card to the circle', () => {
    expect(key(' ', {})).toBeNull()
    expect(key('Enter', {})).toBeNull()
  })
})

describe('applySelectIntent', () => {
  const state = (ids: string[], anchor: string | null = null) => ({ selected: new Set(ids), anchor })

  it('toggle adds a card and makes it the anchor', () => {
    const next = applySelectIntent(state([]), 'toggle', 'b', ORDER)
    expect([...next.selected]).toEqual(['b'])
    expect(next.anchor).toBe('b')
  })

  it('toggle removes a selected card and drops it as the anchor', () => {
    const next = applySelectIntent(state(['a', 'b'], 'b'), 'toggle', 'b', ORDER)
    expect([...next.selected]).toEqual(['a'])
    expect(next.anchor).toBeNull()
  })

  it('toggle keeps another card as the anchor', () => {
    expect(applySelectIntent(state(['a', 'b'], 'a'), 'toggle', 'b', ORDER).anchor).toBe('a')
  })

  it('does not change the state it was given', () => {
    const before = state(['a'], 'a')
    applySelectIntent(before, 'range', 'd', ORDER)
    expect([...before.selected]).toEqual(['a'])
  })

  it('range selects from the anchor forward, inclusive', () => {
    const next = applySelectIntent(state(['b'], 'b'), 'range', 'd', ORDER)
    expect([...next.selected].sort()).toEqual(['b', 'c', 'd'])
    expect(next.anchor).toBe('d')
  })

  it('range selects backward and keeps what was already selected', () => {
    const next = applySelectIntent(state(['e', 'd'], 'd'), 'range', 'b', ORDER)
    expect([...next.selected].sort()).toEqual(['b', 'c', 'd', 'e'])
  })

  it('range with no anchor just selects the card', () => {
    const next = applySelectIntent(state([]), 'range', 'c', ORDER)
    expect([...next.selected]).toEqual(['c'])
    expect(next.anchor).toBe('c')
  })

  it('range ignores an anchor that is no longer selected', () => {
    expect([...applySelectIntent(state(['e'], 'a'), 'range', 'c', ORDER).selected].sort()).toEqual(['c', 'e'])
  })

  it('range ignores an anchor that is not in the grid', () => {
    expect([...applySelectIntent(state(['z'], 'z'), 'range', 'c', ORDER).selected].sort()).toEqual(['c', 'z'])
  })
})
