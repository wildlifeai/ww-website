import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchSettingsProjects, toSettingsProject } from './settingsProjects'

const base = { id: 'p-1', name: 'Ridge rats', description: null, created_at: '2026-10-01T00:00:00Z' }

describe('toSettingsProject', () => {
  it('reads the server count', () => {
    expect(toSettingsProject({ ...base, deployments: [{ count: 42 }] }).deployment_count).toBe(42)
    expect(toSettingsProject({ ...base, deployments: [{ count: 0 }] }).deployment_count).toBe(0)
    expect(toSettingsProject({ ...base, deployments: null }).deployment_count).toBe(0)
  })
})

/** A client whose project read answers with `answer`, recording each select. */
function fakeDb(answer: { data: unknown; error: unknown }) {
  const selects: string[] = []
  const from = vi.fn(() => {
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
    const { db, selects } = fakeDb({ data: [{ ...base, deployments: [{ count: 3 }] }], error: null })
    expect(await fetchSettingsProjects(db)).toEqual([{ ...base, deployment_count: 3 }])
    expect(selects).toEqual(['id, name, description, created_at, deployments(count)'])
  })

  it('throws other errors', async () => {
    const { db } = fakeDb({ data: null, error: { code: '42P01', message: 'missing' } })
    await expect(fetchSettingsProjects(db)).rejects.toThrow('missing')
  })
})
