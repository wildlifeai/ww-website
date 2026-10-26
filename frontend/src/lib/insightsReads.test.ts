import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  browserTimeZone, countDeploymentPhotos, fetchDailyCounts, fetchInsightsDeployments, fetchObservationCounts,
} from './insightsReads'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Page = { data: unknown[] | null; error: { message: string } | null }

/** A client that records each query's calls (a table read or a function call) and answers with
 *  `answer(calls)`. */
function fakeDb(answer: (calls: Call[], index: number) => Page) {
  const queries: Call[][] = []
  const query = (first: Call) => {
    const calls: Call[] = [first]
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
  }
  const db = {
    from: (table: string) => query(['from', table]),
    rpc: (fn: string, args: unknown) => query(['rpc', fn, args]),
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

describe('fetchObservationCounts', () => {
  const deps = [
    { id: 'd1', project_id: 'p1' }, { id: 'd2', project_id: 'p1' }, { id: 'd3', project_id: 'p2' },
  ]

  it('asks once for the projects, not for every deployment', async () => {
    const { db, queries } = fakeDb(() => ({
      data: [
        { deployment_id: 'd1', scientific_name: 'Rattus rattus', observation_type: 'animal', n: 4 },
        { deployment_id: 'd3', scientific_name: null, observation_type: 'blank', n: '2' },
      ],
      error: null,
    }))
    const rows = await fetchObservationCounts(db, deps, false)
    expect(queries).toHaveLength(1)
    expect(queries[0][0]).toEqual(['rpc', 'insights_observation_counts', { p_project_ids: ['p1', 'p2'] }])
    expect(queries[0]).toContainEqual(['range', 0, PAGE_SIZE - 1])
    expect(rows).toEqual([
      { deployment_id: 'd1', scientific_name: 'Rattus rattus', observation_type: 'animal', n: 4 },
      { deployment_id: 'd3', scientific_name: null, observation_type: 'blank', n: 2 },
    ])
  })

  it('names the deployments only when narrowed, and drops those not listed', async () => {
    const { db, queries } = fakeDb(() => ({
      data: [
        { deployment_id: 'd1', scientific_name: 'Mus musculus', observation_type: 'animal', n: 1 },
        { deployment_id: 'd9', scientific_name: 'Mus musculus', observation_type: 'animal', n: 5 },
      ],
      error: null,
    }))
    const rows = await fetchObservationCounts(db, [deps[0]], true)
    expect(queries[0][0]).toEqual(['rpc', 'insights_observation_counts', { p_project_ids: ['p1'], p_deployment_ids: ['d1'] }])
    expect(rows.map(r => r.deployment_id)).toEqual(['d1'])
    expect(await fetchObservationCounts(db, [], false)).toEqual([])
    expect(queries).toHaveLength(1)
  })

  it('pages past 1,000 rows and throws when a request fails', async () => {
    const row = { deployment_id: 'd1', scientific_name: 'A a', observation_type: 'animal', n: 1 }
    const { db, queries } = fakeDb((_, i) => ({ data: Array(i === 0 ? PAGE_SIZE : 1).fill(row), error: null }))
    expect(await fetchObservationCounts(db, deps, false)).toHaveLength(PAGE_SIZE + 1)
    expect(queries[1]).toContainEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
    const failing = fakeDb(() => ({ data: null, error: { message: 'denied' } }))
    await expect(fetchObservationCounts(failing.db, deps, false)).rejects.toThrow('denied')
  })
})

describe('fetchDailyCounts', () => {
  it('asks for the deployment in the page zone, every species', async () => {
    const { db, queries } = fakeDb(() => ({
      data: [{ deployment_id: 'd1', day: null, scientific_name: 'Rattus rattus', n: '3' }],
      error: null,
    }))
    expect(await fetchDailyCounts(db, 'd1', 'Pacific/Auckland')).toEqual([
      { deployment_id: 'd1', day: null, scientific_name: 'Rattus rattus', n: 3 },
    ])
    expect(queries[0][0]).toEqual(['rpc', 'deployment_daily_counts', { p_deployment_ids: ['d1'], p_fallback_tz: 'Pacific/Auckland' }])
  })

  it('falls back to the browser zone', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchDailyCounts(db, 'd1', null)
    expect(queries[0][0]).toEqual(['rpc', 'deployment_daily_counts', { p_deployment_ids: ['d1'], p_fallback_tz: browserTimeZone() }])
    expect(browserTimeZone()).toBeTruthy()
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
