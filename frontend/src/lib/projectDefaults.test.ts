import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { PROJECT_DEFAULT_COLUMNS, saveProjectDefaults, type ProjectDefaults } from './projectDefaults'
import { NOT_ALLOWED } from './projectDetails'

const stored: ProjectDefaults = {
  id: 'p-1', name: 'Ridge rats', capture_method_id: 1, model_id: null, photos_per_trigger: 3,
  photo_interval_milliseconds: 1000, detection_threshold_pct: 57, flash_mode: 'off', flash_led: 'ir',
  flash_window_start_minutes_utc: null, flash_window_minutes: null, lorawan_required: false, lorawan_ping_minutes: 240,
}

/** A projects query builder that records its calls and resolves to `answer`. */
function fakeDb(answer: { data: unknown; error: unknown }) {
  const calls: unknown[][] = []
  const builder: Record<string, unknown> = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => Promise.resolve(answer).then(resolve, reject),
  }
  for (const m of ['select', 'update', 'eq']) {
    builder[m] = (...args: unknown[]) => { calls.push([m, ...args]); return builder }
  }
  const from = vi.fn(() => builder)
  return { db: { from } as unknown as SupabaseClient, calls, from }
}

describe('saveProjectDefaults', () => {
  it('reads the LoRaWAN columns with the rest', () => {
    expect(PROJECT_DEFAULT_COLUMNS).toContain('lorawan_required')
    expect(PROJECT_DEFAULT_COLUMNS).toContain('lorawan_ping_minutes')
  })

  it('updates as the user, asks for the row back and returns what the database holds', async () => {
    const saved = { ...stored, lorawan_required: true, lorawan_ping_minutes: 360 }
    const { db, calls, from } = fakeDb({ data: [saved], error: null })
    await expect(saveProjectDefaults(db, 'p-1', { lorawan_ping_minutes: 360 })).resolves.toEqual({ saved })
    expect(from).toHaveBeenCalledWith('projects')
    expect(calls).toEqual([['update', { lorawan_ping_minutes: 360 }], ['eq', 'id', 'p-1'], ['select', PROJECT_DEFAULT_COLUMNS]])
  })

  it('treats 0 rows as refused, since RLS does not raise an error for a member', async () => {
    await expect(saveProjectDefaults(fakeDb({ data: [], error: null }).db, 'p-1', { lorawan_required: true }))
      .resolves.toEqual({ message: NOT_ALLOWED })
  })

  it('words a refused value and any other error', async () => {
    const check = { code: '23514', message: 'violates check constraint "projects_lorawan_ping_minutes_check"' }
    await expect(saveProjectDefaults(fakeDb({ data: null, error: check }).db, 'p-1', { lorawan_ping_minutes: 30 }))
      .resolves.toEqual({ message: `That value is not allowed: ${check.message}` })
    await expect(saveProjectDefaults(fakeDb({ data: null, error: { code: '08006', message: 'connection lost' } }).db, 'p-1', { lorawan_required: true }))
      .resolves.toEqual({ message: 'Not saved: connection lost' })
  })
})
