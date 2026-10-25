import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  countDeploymentPhotos, fetchInsightsDeployments, fetchInsightsObservations, IDS_PER_REQUEST,
} from './insightsReads'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Page = { data: unknown[] | null; error: { message: string } | null }

/** A client that records each query's calls and answers with `answer(calls)`. */
function fakeDb(answer: (calls: Call[], index: number) => Page) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      const index = queries.length
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'is', 'or', 'order', 'not']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => {
        calls.push(['range', ...args])
        return Promise.resolve(answer(calls, index))
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

describe('fetchInsightsDeployments', () => {
  it('pages past 1,000 deployments and flattens the project and device names', async () => {
    const page = (n: number) => Array.from({ length: n }, (_, i) => ({
      id: `d${i}`, project_id: 'p1', projects: { name: 'Hut' }, devices: null,
    }))
    const { db, queries } = fakeDb((_, i) => ({ data: page(i === 0 ? PAGE_SIZE : 3), error: null }))
    const rows = await fetchInsightsDeployments(db, ['p1'], [])
    expect(rows).toHaveLength(PAGE_SIZE + 3)
    expect(rows[0]).toEqual({ id: 'd0', project_id: 'p1', project_name: 'Hut', device_name: '-' })
    expect(queries[0]).toContainEqual(['in', 'project_id', ['p1']])
    expect(queries[1]).toContainEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
  })

  it('includes deep-linked deployments beside the selected projects', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchInsightsDeployments(db, ['p1', 'p2'], ['d9'])
    expect(queries[0]).toContainEqual(['or', 'project_id.in.(p1,p2),id.in.(d9)'])
  })

  it('reads only the linked deployments when no project is selected', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchInsightsDeployments(db, null, ['d9'])
    expect(queries[0]).toContainEqual(['in', 'id', ['d9']])
    expect(await fetchInsightsDeployments(db, null, [])).toEqual([])
    expect(queries).toHaveLength(1)
  })
})

describe('fetchInsightsObservations', () => {
  it('splits a long deployment list across requests', async () => {
    const ids = Array.from({ length: IDS_PER_REQUEST + 1 }, (_, i) => `d${i}`)
    const { db, queries } = fakeDb(calls => {
      const inCall = calls.find(c => c[0] === 'in') as [string, string, string[]]
      return { data: inCall[2].map(id => ({ id: `o-${id}`, deployment_id: id, media: null })), error: null }
    })
    const rows = await fetchInsightsObservations(db, ids)
    expect(queries).toHaveLength(2)
    expect((queries[1].find(c => c[0] === 'in') as unknown[])[2]).toEqual([`d${IDS_PER_REQUEST}`])
    expect(rows).toHaveLength(IDS_PER_REQUEST + 1)
  })

  it('reads the photo capture time in the same request', async () => {
    const { db, queries } = fakeDb(() => ({
      data: [
        { id: 'o1', deployment_id: 'd1', media: { deleted_at: null, timestamp: '2026-09-01T23:30:00Z' } },
        { id: 'o2', deployment_id: 'd1', media: { deleted_at: null, timestamp: null } },
        { id: 'o3', deployment_id: 'd1', media: null },
      ],
      error: null,
    }))
    const rows = await fetchInsightsObservations(db, ['d1'])
    expect(queries).toHaveLength(1)
    expect(queries[0].find(c => c[0] === 'select')).toEqual(
      ['select', 'id, deployment_id, scientific_name, observation_type, media(deleted_at, timestamp)'])
    expect(rows).toEqual([
      { id: 'o1', deployment_id: 'd1', captured_at: '2026-09-01T23:30:00Z' },
      { id: 'o2', deployment_id: 'd1', captured_at: null },
      { id: 'o3', deployment_id: 'd1', captured_at: null },
    ])
  })

  it('throws when a request fails', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'denied' } }))
    await expect(fetchInsightsObservations(db, ['d1'])).rejects.toThrow('denied')
  })
})

describe('countDeploymentPhotos', () => {
  /** A head-count query: the chain itself is awaited, with no range. */
  function countDb(result: { count: number | null; error: { message: string } | null }) {
    const calls: Call[] = []
    const chain: Record<string, unknown> = {
      then: (resolve: (v: unknown) => void) => resolve(result),
    }
    for (const m of ['from', 'select', 'eq', 'is']) {
      chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
    }
    return { db: chain as unknown as SupabaseClient, calls }
  }

  it('counts the live photos without reading them', async () => {
    const { db, calls } = countDb({ count: 12, error: null })
    expect(await countDeploymentPhotos(db, 'd1')).toBe(12)
    expect(calls).toEqual([
      ['from', 'media'],
      ['select', 'id', { count: 'exact', head: true }],
      ['eq', 'deployment_id', 'd1'],
      ['is', 'deleted_at', null],
    ])
  })

  it('throws rather than report a false zero', async () => {
    const { db } = countDb({ count: null, error: { message: 'denied' } })
    await expect(countDeploymentPhotos(db, 'd1')).rejects.toThrow('denied')
  })
})
