import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { canEditObservations, deleteObservation, NOT_REMOVED } from './observationWrites'

/** A client whose every query chain resolves to `result`, recording rpc calls. */
function fakeDb(result: { data: unknown; error: { message: string } | null }, rpc?: { data: unknown; error: null }) {
  const chain: Record<string, unknown> = {}
  for (const m of ['from', 'delete', 'eq', 'select']) chain[m] = vi.fn(() => chain)
  chain.maybeSingle = vi.fn(async () => result)
  chain.then = (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve)
  chain.rpc = vi.fn(async () => rpc)
  return chain as unknown as SupabaseClient & { rpc: ReturnType<typeof vi.fn> }
}

describe('deleteObservation', () => {
  it('resolves when the row comes back deleted', async () => {
    await expect(deleteObservation(fakeDb({ data: [{ id: 'o1' }], error: null }), 'o1')).resolves.toBeUndefined()
  })

  it('reports a delete that matched no row as not removed', async () => {
    await expect(deleteObservation(fakeDb({ data: [], error: null }), 'o1')).rejects.toThrow(NOT_REMOVED)
  })

  it('passes a database error through', async () => {
    await expect(deleteObservation(fakeDb({ data: null, error: { message: 'boom' } }), 'o1')).rejects.toThrow('boom')
  })
})

describe('canEditObservations', () => {
  it('asks has_project_role for member rights on the deployment’s project', async () => {
    const db = fakeDb({ data: { project_id: 'p1', deleted_at: null }, error: null }, { data: true, error: null })
    expect(await canEditObservations(db, 'u1', 'd1')).toBe(true)
    expect(db.rpc).toHaveBeenCalledWith('has_project_role', { user_id: 'u1', project_id: 'p1', required_role: 'project_member' })
  })

  it('says no for a deployment the user cannot see or that is deleted', async () => {
    expect(await canEditObservations(fakeDb({ data: null, error: null }), 'u1', 'd1')).toBe(false)
    expect(await canEditObservations(fakeDb({ data: { project_id: 'p1', deleted_at: '2026-10-01' }, error: null }), 'u1', 'd1')).toBe(false)
  })
})
