import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { AGGREGATES_DISABLED, fetchSettingsProjects, toSettingsProject } from './settingsProjects'

const base = { id: 'p-1', name: 'Ridge rats', description: null, created_at: '2026-10-01T00:00:00Z' }

describe('toSettingsProject', () => {
  it('reads the server count', () => {
    expect(toSettingsProject({ ...base, deployments: [{ count: 42 }] }).deployment_count).toBe(42)
    expect(toSettingsProject({ ...base, deployments: [{ count: 0 }] }).deployment_count).toBe(0)
  })

  it('counts ids when the server sent them instead', () => {
    expect(toSettingsProject({ ...base, deployments: [{ id: 'd-1' }] }).deployment_count).toBe(1)
    expect(toSettingsProject({ ...base, deployments: [{ id: 'd-1' }, { id: 'd-2' }] }).deployment_count).toBe(2)
    expect(toSettingsProject({ ...base, deployments: null }).deployment_count).toBe(0)
  })
})

/** A client whose project reads answer in turn from `answers`, recording each select. */
function fakeDb(answers: { data: unknown; error: unknown }[]) {
  const selects: string[] = []
  const from = vi.fn(() => {
    const answer = answers[selects.length] ?? answers[answers.length - 1]
    const b: Record<string, unknown> = {
      then: (resolve: (v: unknown) => unknown) => Promise.resolve(answer).then(resolve),
    }
    b.select = (cols: string) => { selects.push(cols); return b }
    for (const m of ['is', 'order']) b[m] = () => b
    return b
  })
  return { db: { from } as unknown as SupabaseClient, selects }
}

describe('fetchSettingsProjects', () => {
  it('asks the server to count', async () => {
    const { db, selects } = fakeDb([{ data: [{ ...base, deployments: [{ count: 3 }] }], error: null }])
    expect(await fetchSettingsProjects(db)).toEqual([{ ...base, deployment_count: 3 }])
    expect(selects).toEqual(['id, name, description, created_at, deployments(count)'])
  })

  it('falls back to ids when aggregates are off', async () => {
    const { db, selects } = fakeDb([
      { data: null, error: { code: AGGREGATES_DISABLED, message: 'Use of aggregate functions is not allowed' } },
      { data: [{ ...base, deployments: [{ id: 'd-1' }, { id: 'd-2' }] }], error: null },
    ])
    expect((await fetchSettingsProjects(db))[0].deployment_count).toBe(2)
    expect(selects[1]).toBe('id, name, description, created_at, deployments(id)')
  })

  it('throws other errors', async () => {
    const { db } = fakeDb([{ data: null, error: { code: '42P01', message: 'missing' } }])
    await expect(fetchSettingsProjects(db)).rejects.toThrow('missing')
  })
})
