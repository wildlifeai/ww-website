import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  chunk, fetchActiveDeployments, fetchLatestTelemetry, newestPerDeployment, summariseDetections,
  ID_CHUNK, TELEMETRY_WINDOW, type MessageRow,
} from './realtimeReads'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Answer = { data: unknown[] | null; error: { message: string } | null }

/**
 * A client that records each query's calls and answers in turn. A query ends at `range` or
 * `limit`; `answer` picks the reply from the recorded calls.
 */
function fakeDb(answer: (calls: Call[], n: number) => Answer) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'is', 'or', 'order', 'gte', 'not']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      for (const end of ['range', 'limit']) {
        chain[end] = (...args: unknown[]) => {
          calls.push([end, ...args])
          return Promise.resolve(answer(calls, queries.length - 1))
        }
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const message = (deployment: string, at: string, battery = 80): MessageRow => ({
  deployment_id: deployment,
  received_at: at,
  lorawan_parsed_messages: [{ battery_level: battery, sd_card_used_capacity: 10 }],
})

describe('chunk', () => {
  it('splits into pieces of at most the size', () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]])
    expect(chunk([], 2)).toEqual([])
  })
})

describe('newestPerDeployment', () => {
  it('keeps the first row of each deployment and skips rows with none', () => {
    const rows = [
      message('d1', '2026-10-21T10:00:00Z', 50),
      message('d2', '2026-10-21T09:00:00Z'),
      message('d1', '2026-10-20T10:00:00Z', 90),
      { deployment_id: null, received_at: '2026-10-21T11:00:00Z', lorawan_parsed_messages: null },
    ]
    expect(newestPerDeployment(rows)).toEqual({
      d1: { battery: 50, sdUsed: 10, lastSeen: '2026-10-21T10:00:00Z' },
      d2: { battery: 80, sdUsed: 10, lastSeen: '2026-10-21T09:00:00Z' },
    })
  })

  it('reads a parsed message given as an object or missing', () => {
    expect(newestPerDeployment([
      { deployment_id: 'd1', received_at: 't', lorawan_parsed_messages: { battery_level: 5, sd_card_used_capacity: null } },
      { deployment_id: 'd2', received_at: 't', lorawan_parsed_messages: null },
    ])).toEqual({
      d1: { battery: 5, sdUsed: null, lastSeen: 't' },
      d2: { battery: null, sdUsed: null, lastSeen: 't' },
    })
  })
})

describe('fetchLatestTelemetry', () => {
  it('makes one read when the window is not full', async () => {
    const { db, queries } = fakeDb(() => ({ data: [message('d1', 't1')], error: null }))
    expect(await fetchLatestTelemetry(db, ['d1', 'd2'])).toEqual({
      d1: { battery: 80, sdUsed: 10, lastSeen: 't1' },
    })
    expect(queries).toHaveLength(1)
    expect(queries[0]).toContainEqual(['limit', TELEMETRY_WINDOW])
  })

  it('reads the newest message of each deployment a full window missed', async () => {
    const full = Array.from({ length: TELEMETRY_WINDOW }, () => message('chatty', 't9'))
    const { db, queries } = fakeDb((_, n) => n === 0
      ? { data: full, error: null }
      : { data: [
        { id: 'quiet', lorawan_messages: [{ received_at: 't1', lorawan_parsed_messages: [{ battery_level: 15, sd_card_used_capacity: 95 }] }] },
        { id: 'never', lorawan_messages: [] },
      ], error: null })
    expect(await fetchLatestTelemetry(db, ['chatty', 'quiet', 'never'])).toEqual({
      chatty: { battery: 80, sdUsed: 10, lastSeen: 't9' },
      quiet: { battery: 15, sdUsed: 95, lastSeen: 't1' },
    })
    expect(queries).toHaveLength(2)
    expect(queries[1]).toEqual([
      ['from', 'deployments'],
      ['select', 'id, lorawan_messages(received_at, lorawan_parsed_messages(battery_level, sd_card_used_capacity))'],
      ['in', 'id', ['quiet', 'never']],
      ['order', 'received_at', { referencedTable: 'lorawan_messages', ascending: false }],
      ['limit', 1, { referencedTable: 'lorawan_messages' }],
    ])
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
