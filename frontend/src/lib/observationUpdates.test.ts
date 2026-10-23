import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { NOT_SAVED, updateObservations } from './observationWrites'

/** A client whose update chain resolves to `result` and records each call. */
function fakeDb(result: { data: unknown; error: { message: string } | null }) {
  const chain: Record<string, ReturnType<typeof vi.fn> | unknown> = {}
  for (const m of ['from', 'update', 'in', 'select']) chain[m] = vi.fn(() => chain)
  chain.then = (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve)
  return chain as unknown as SupabaseClient & Record<'from' | 'update' | 'in' | 'select', ReturnType<typeof vi.fn>>
}

describe('updateObservations', () => {
  it('returns the ids the database changed, and asks for them with select(id)', async () => {
    const db = fakeDb({ data: [{ id: 'o1' }, { id: 'o2' }], error: null })
    await expect(updateObservations(db, ['o1', 'o2'], { review_status: 'human_reviewed' })).resolves.toEqual(['o1', 'o2'])
    expect(db.from).toHaveBeenCalledWith('observations')
    expect(db.update).toHaveBeenCalledWith({ review_status: 'human_reviewed' })
    expect(db.in).toHaveBeenCalledWith('id', ['o1', 'o2'])
    expect(db.select).toHaveBeenCalledWith('id')
  })

  it('throws NOT_SAVED when RLS changed no row, the refusal a project_viewer gets', async () => {
    await expect(updateObservations(fakeDb({ data: [], error: null }), ['o1'], { scientific_name: 'x' })).rejects.toThrow(NOT_SAVED)
    await expect(updateObservations(fakeDb({ data: null, error: null }), ['o1'], { scientific_name: 'x' })).rejects.toThrow(NOT_SAVED)
  })

  it('passes a database error through', async () => {
    await expect(updateObservations(fakeDb({ data: null, error: { message: 'boom' } }), ['o1'], {})).rejects.toThrow('boom')
  })

  it('sends nothing for an empty list', async () => {
    const db = fakeDb({ data: [], error: null })
    await expect(updateObservations(db, [], { scientific_name: 'x' })).resolves.toEqual([])
    expect(db.from).not.toHaveBeenCalled()
  })

  it('returns only the rows that changed when RLS refused some', async () => {
    const db = fakeDb({ data: [{ id: 'o2' }], error: null })
    await expect(updateObservations(db, ['o1', 'o2'], { review_status: 'human_reviewed' })).resolves.toEqual(['o2'])
  })
})
