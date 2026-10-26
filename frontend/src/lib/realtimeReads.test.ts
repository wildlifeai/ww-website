import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  fetchActiveDeployments, fetchDeploymentUpdate, fetchLatestTelemetry, summariseDetections, withEntry,
  ID_CHUNK,
} from './realtimeReads'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Answer = { data: unknown[] | null; error: { message: string } | null }

/**
 * A client that records each query's calls and answers in turn. A query ends at `range`, or
 * when the chain itself is awaited; `answer` picks the reply from the recorded calls.
 */
function fakeDb(answer: (calls: Call[], n: number) => Answer) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      const n = queries.length
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'is', 'or', 'order', 'gte', 'not']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => {
        calls.push(['range', ...args])
        return Promise.resolve(answer(calls, n))
      }
      chain.then = (resolve: (v: Answer) => void) => resolve(answer(calls, n))
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const latest = (deployment: string, at: string, battery: number | null = 80) => ({
  deployment_id: deployment, received_at: at, battery_level: battery, sd_card_used_capacity: 10,
})

describe('fetchLatestTelemetry', () => {
  it('reads the view once for the deployments and maps each row', async () => {
    const { db, queries } = fakeDb(() => ({ data: [latest('d1', 't1'), latest('d3', 't2', null)], error: null }))
    expect(await fetchLatestTelemetry(db, ['d1', 'd2', 'd3'])).toEqual({
      d1: { battery: 80, sdUsed: 10, lastSeen: 't1' },
      d3: { battery: null, sdUsed: 10, lastSeen: 't2' },
    })
    expect(queries).toEqual([[
      ['from', 'deployment_latest_lorawan'],
      ['select', 'deployment_id, received_at, battery_level, sd_card_used_capacity'],
      ['in', 'deployment_id', ['d1', 'd2', 'd3']],
    ]])
  })

  it('sends the ids in chunks', async () => {
    const ids = Array.from({ length: ID_CHUNK + 1 }, (_, i) => `d${i}`)
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchLatestTelemetry(db, ids)
    expect(queries.map(q => (q.find(c => c[0] === 'in')?.[2] as string[]).length)).toEqual([ID_CHUNK, 1])
  })

  it('throws a refused read instead of showing every camera as awaiting', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'permission denied' } }))
    await expect(fetchLatestTelemetry(db, ['d1'])).rejects.toThrow('permission denied')
  })
})

describe('fetchActiveDeployments', () => {
  it('pages past 1,000 rows with a stable order and names the project and device', async () => {
    const row = (i: number) => ({ id: `d${i}`, project_id: 'p1', projects: { name: 'P' }, devices: null })
    const { db, queries } = fakeDb((_, n) => n === 0
      ? { data: Array.from({ length: PAGE_SIZE }, (_, i) => row(i)), error: null }
      : { data: [row(PAGE_SIZE)], error: null })
    const deps = await fetchActiveDeployments(db, ['p1'], new Date('2026-10-21T00:00:00Z'))
    expect(deps).toHaveLength(PAGE_SIZE + 1)
    expect(deps[0]).toEqual({ id: 'd0', project_id: 'p1', project_name: 'P', device_name: '-' })
    expect(queries[0]).toContainEqual(['or', 'deployment_end.is.null,deployment_end.gt.2026-10-21T00:00:00.000Z'])
    expect(queries[0]).toContainEqual(['order', 'id'])
    expect(queries[1]).toContainEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
  })

  it('sends no request for no projects', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchActiveDeployments(db, [])).toEqual([])
    expect(queries).toHaveLength(0)
  })
})

describe('fetchDeploymentUpdate', () => {
  it("reads one deployment's telemetry and detections, two requests", async () => {
    const { db, queries } = fakeDb(calls => calls[0][1] === 'deployment_latest_lorawan'
      ? { data: [latest('d1', 't9', 55)], error: null }
      : { data: [{ deployment_id: 'd1', scientific_name: 'Rattus rattus', created_at: 't8' }], error: null })
    expect(await fetchDeploymentUpdate(db, 'd1', Date.parse('2026-10-21T00:00:00Z'))).toEqual({
      telemetry: { battery: 55, sdUsed: 10, lastSeen: 't9' },
      detections: { count: 1, latest: 't8', top: 'Rattus rattus' },
    })
    expect(queries).toHaveLength(2)
    expect(queries.map(q => q.find(c => c[0] === 'in'))).toEqual([
      ['in', 'deployment_id', ['d1']], ['in', 'deployment_id', ['d1']],
    ])
  })

  it('gives undefined for what the deployment does not have', async () => {
    const { db } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchDeploymentUpdate(db, 'd1')).toEqual({ telemetry: undefined, detections: undefined })
  })
})

describe('withEntry', () => {
  it('replaces one entry and leaves the map it was given alone', () => {
    const map = { d1: 1, d2: 2 }
    expect(withEntry(map, 'd1', 5)).toEqual({ d1: 5, d2: 2 })
    expect(withEntry(map, 'd3', 3)).toEqual({ d1: 1, d2: 2, d3: 3 })
    expect(withEntry(map, 'd2', undefined)).toEqual({ d1: 1 })
    expect(map).toEqual({ d1: 1, d2: 2 })
  })
})

describe('summariseDetections', () => {
  it('counts per deployment and names the latest species', () => {
    expect(summariseDetections([
      { deployment_id: 'd1', scientific_name: 'Rattus rattus', created_at: '2026-10-21T01:00:00Z' },
      { deployment_id: 'd1', scientific_name: 'Mus musculus', created_at: '2026-10-21T03:00:00Z' },
      { deployment_id: 'd1', scientific_name: null, created_at: '2026-10-21T02:00:00Z' },
      { deployment_id: 'd2', scientific_name: null, created_at: '2026-10-20T01:00:00Z' },
    ])).toEqual({
      d1: { count: 3, latest: '2026-10-21T03:00:00Z', top: 'Mus musculus' },
      d2: { count: 1, latest: '2026-10-20T01:00:00Z', top: null },
    })
  })
})
