import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchDistinctValues, MAX_REQUESTS } from './distinctValues'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]

/** A client over one sorted column: answers `gt` and `limit` the way PostgREST would. */
function fakeDb(sorted: string[]) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      queries.push(calls)
      let after: string | null = null
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'is', 'not', 'eq', 'order']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.gt = (col: string, v: string) => { calls.push(['gt', col, v]); after = v; return chain }
      chain.limit = (n: number) => {
        calls.push(['limit', n])
        const rows = sorted.filter(v => after === null || v > after).slice(0, n).map(v => ({ scientific_name: v }))
        return Promise.resolve({ data: rows, error: null })
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const many = (name: string, n: number) => Array.from({ length: n }, () => name)

describe('fetchDistinctValues', () => {
  it('reads one page when the rows fit in it', async () => {
    const { db, queries } = fakeDb(['Mus', 'Mus', 'Rattus'])
    const out = await fetchDistinctValues(db, { column: 'scientific_name', deploymentIds: ['d1'] })
    expect(out).toEqual({ values: ['Mus', 'Rattus'], incomplete: false })
    expect(queries).toHaveLength(1)
  })

  it('skips past a value with thousands of rows instead of paging through them', async () => {
    const rows = [...many('Aves', 2500), ...many('Mus', 3000), 'Rattus', 'Zosterops']
    const { db, queries } = fakeDb(rows)
    const out = await fetchDistinctValues(db, { column: 'scientific_name', deploymentIds: ['d1'] })
    expect(out.values).toEqual(['Aves', 'Mus', 'Rattus', 'Zosterops'])
    // Page 1 is all Aves, page 2 (after Aves) is all Mus, page 3 (after Mus) holds the rest.
    expect(queries).toHaveLength(3)
    expect(queries[1]).toContainEqual(['gt', 'scientific_name', 'Aves'])
    expect(queries[2]).toContainEqual(['gt', 'scientific_name', 'Mus'])
  })

  it('reads live rows of the chosen deployments, with any extra filter', async () => {
    const { db, queries } = fakeDb(['a@b.nz'])
    await fetchDistinctValues(db, { column: 'classified_by', deploymentIds: ['d1', 'd2'], where: { classification_method: 'human' } })
    expect(queries[0]).toEqual(expect.arrayContaining([
      ['select', 'classified_by, media!inner(deleted_at)'],
      ['in', 'deployment_id', ['d1', 'd2']],
      ['is', 'deleted_at', null],
      ['is', 'media.deleted_at', null],
      ['not', 'classified_by', 'is', null],
      ['eq', 'classification_method', 'human'],
      ['order', 'classified_by'],
      ['limit', PAGE_SIZE],
    ]))
  })

  it('asks nothing for no deployments', async () => {
    const { db, queries } = fakeDb(['x'])
    expect(await fetchDistinctValues(db, { column: 'scientific_name', deploymentIds: [] })).toEqual({ values: [], incomplete: false })
    expect(queries).toHaveLength(0)
  })

  it('stops after MAX_REQUESTS and says the list is incomplete', async () => {
    const rows = Array.from({ length: MAX_REQUESTS + 2 }, (_, i) => many(`s${String(i).padStart(3, '0')}`, PAGE_SIZE)).flat()
    const { db, queries } = fakeDb(rows)
    const out = await fetchDistinctValues(db, { column: 'scientific_name', deploymentIds: ['d1'] })
    expect(out.incomplete).toBe(true)
    expect(queries).toHaveLength(MAX_REQUESTS)
  })

  it('passes a database error through', async () => {
    const db = {
      from: () => {
        const chain: Record<string, unknown> = {}
        for (const m of ['select', 'in', 'is', 'not', 'eq', 'order', 'gt']) chain[m] = () => chain
        chain.limit = () => Promise.resolve({ data: null, error: { message: 'boom' } })
        return chain
      },
    } as unknown as SupabaseClient
    await expect(fetchDistinctValues(db, { column: 'scientific_name', deploymentIds: ['d1'] })).rejects.toThrow('boom')
  })
})
