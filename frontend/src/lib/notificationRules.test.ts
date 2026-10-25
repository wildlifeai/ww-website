import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { NOT_ALLOWED, ruleFor, saveRule, toggleChannel, type Rule } from './notificationRules'

const rule: Rule = {
  project_id: 'p-1', event_type: 'species_detection', species_filter: ' rat ', channels: ['web'],
  digest: 'immediate', is_active: true,
}

describe('rule helpers', () => {
  it('finds the stored rule or gives an inactive default', () => {
    expect(ruleFor([rule], 'p-1', 'species_detection')).toBe(rule)
    expect(ruleFor([rule], 'p-1', 'camera_silent')).toMatchObject({ channels: [], is_active: false, species_filter: null })
  })

  it('toggles a channel on and off', () => {
    expect(toggleChannel(['web'], 'email')).toEqual(['web', 'email'])
    expect(toggleChannel(['web', 'email'], 'web')).toEqual(['email'])
  })
})

function fakeDb(answer: { data: unknown; error: unknown }) {
  const upsert = vi.fn()
  const builder: Record<string, unknown> = {
    upsert: (...args: unknown[]) => { upsert(...args); return builder },
    select: () => Promise.resolve(answer),
  }
  return { db: { from: () => builder } as unknown as SupabaseClient, upsert }
}

describe('saveRule', () => {
  it('trims the filter, derives is_active and returns the stored row', async () => {
    const stored = { ...rule, species_filter: 'rat' }
    const { db, upsert } = fakeDb({ data: [stored], error: null })
    expect(await saveRule(db, 'u-1', rule)).toEqual(stored)
    expect(upsert.mock.calls[0][0]).toMatchObject({ user_id: 'u-1', species_filter: 'rat', is_active: true })
  })

  it('reports a refusal instead of saving', async () => {
    await expect(saveRule(fakeDb({ data: null, error: { code: '42501', message: 'rls' } }).db, 'u-1', rule))
      .rejects.toThrow(NOT_ALLOWED)
    await expect(saveRule(fakeDb({ data: [], error: null }).db, 'u-1', rule)).rejects.toThrow(NOT_ALLOWED)
  })

  it('passes other errors on', async () => {
    await expect(saveRule(fakeDb({ data: null, error: { code: '23514', message: 'check' } }).db, 'u-1', rule))
      .rejects.toThrow('Not saved: check')
  })
})
