import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchLiveObservations, PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Page = { data: unknown[] | null; error: { message: string } | null }

/** A client that records each query's calls and answers the pages in turn. */
function fakeDb(pages: Page[]) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'is', 'or', 'order', 'not']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => {
        calls.push(['range', ...args])
        return Promise.resolve(pages[queries.length - 1])
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const rows = (n: number, offset = 0) =>
  Array.from({ length: n }, (_, i) => ({ id: `o${offset + i}`, media: { deleted_at: null } }))

describe('fetchLiveObservations', () => {
  it('keeps observations with no photo or a live photo, and drops the embed', async () => {
    const { db, queries } = fakeDb([{ data: rows(2), error: null }])
    const r = await fetchLiveObservations(db, {
      columns: 'id, deployment_id',
      filter: q => q.in('deployment_id', ['d1']),
    })
    expect(r).toEqual({ data: [{ id: 'o0' }, { id: 'o1' }], error: null })
    expect(queries[0]).toEqual([
      ['from', 'observations'],
      ['select', 'id, deployment_id, media(deleted_at)'],
      ['in', 'deployment_id', ['d1']],
      ['is', 'deleted_at', null],
      ['is', 'media.deleted_at', null],
      ['or', 'media_id.is.null,media.not.is.null'],
      ['order', 'id'],
      ['range', 0, PAGE_SIZE - 1],
    ])
  })

  it('with requirePhoto joins live photos only and returns the media columns', async () => {
    const { db, queries } = fakeDb([{ data: [{ id: 'o0', media: { deleted_at: null, timestamp: 't' } }], error: null }])
    const r = await fetchLiveObservations(db, { columns: 'id', mediaColumns: 'timestamp', requirePhoto: true })
    expect(r.data).toEqual([{ id: 'o0', media: { deleted_at: null, timestamp: 't' } }])
    expect(queries[0][1]).toEqual(['select', 'id, media!inner(deleted_at, timestamp)'])
    expect(queries[0].some(c => c[0] === 'or')).toBe(false)
  })

  it('reads past the 1,000-row cap, page by page, until a short page', async () => {
    const { db, queries } = fakeDb([
      { data: rows(PAGE_SIZE), error: null },
      { data: rows(3, PAGE_SIZE), error: null },
    ])
    const r = await fetchLiveObservations(db, { columns: 'id', order: { column: 'created_at' } })
    expect(r.data).toHaveLength(PAGE_SIZE + 3)
    expect(queries.map(q => q.at(-1))).toEqual([['range', 0, PAGE_SIZE - 1], ['range', PAGE_SIZE, 2 * PAGE_SIZE - 1]])
    expect(queries[0]).toContainEqual(['order', 'created_at', { ascending: true }])
  })

  it('passes a database error through', async () => {
    const { db } = fakeDb([{ data: null, error: { message: 'boom' } }])
    expect(await fetchLiveObservations(db, { columns: 'id' })).toEqual({ data: null, error: { message: 'boom' } })
  })
})
