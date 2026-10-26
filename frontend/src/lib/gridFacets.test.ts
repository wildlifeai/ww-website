import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchGridFacets, gridFacetOptions, IDS_PER_CALL, type FacetRow } from './gridFacets'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]

/** A client whose `grid_facets` answers each call with `answer(ids)`, paged by `range`. */
function fakeDb(answer: (ids: string[]) => FacetRow[]) {
  const calls: Call[][] = []
  const db = {
    rpc: (name: string, args: { p_deployment_ids: string[] }) => {
      const log: Call[] = [['rpc', name, args]]
      calls.push(log)
      const chain: Record<string, unknown> = {}
      chain.order = (...a: unknown[]) => { log.push(['order', ...a]); return chain }
      chain.range = (from: number, to: number) => {
        log.push(['range', from, to])
        return Promise.resolve({ data: answer(args.p_deployment_ids).slice(from, to + 1), error: null })
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, calls }
}

const row = (facet: string, value: string | null, n: number | string): FacetRow => ({ facet, value, n })

describe('gridFacetOptions', () => {
  it('splits the rows into the three lists, alphabetical whatever the case', () => {
    const out = gridFacetOptions([
      row('scientific_name', 'Rattus rattus', 4),
      row('scientific_name', 'aves', 2),
      row('scientific_name', 'Mus musculus', 9),
      row('source_model_version', 'speciesnet-4.0.1a', 13),
      row('source_model_version', 'bioclip-2', 1),
      row('classified_by', 'kiri@example.org', 3),
    ])
    expect(out.species).toEqual([{ value: 'aves', n: 2 }, { value: 'Mus musculus', n: 9 }, { value: 'Rattus rattus', n: 4 }])
    expect(out.models.map(o => o.value)).toEqual(['bioclip-2', 'speciesnet-4.0.1a'])
    expect(out.annotators).toEqual([{ value: 'kiri@example.org', n: 3 }])
  })

  it('adds up a value found in several chunks, and reads a bigint sent as text', () => {
    const out = gridFacetOptions([row('scientific_name', 'Mus musculus', 2), row('scientific_name', 'Mus musculus', '5')])
    expect(out.species).toEqual([{ value: 'Mus musculus', n: 7 }])
  })

  it('drops an unknown facet and a missing value', () => {
    const out = gridFacetOptions([row('sex', 'male', 1), row('scientific_name', null, 3)])
    expect(out).toEqual({ species: [], models: [], annotators: [] })
  })
})

describe('fetchGridFacets', () => {
  it('asks once for a short selection, in a stable order', async () => {
    const { db, calls } = fakeDb(() => [row('scientific_name', 'Mus musculus', 1)])
    const out = await fetchGridFacets(db, ['d1', 'd2'])
    expect(out.species).toEqual([{ value: 'Mus musculus', n: 1 }])
    expect(calls).toEqual([[
      ['rpc', 'grid_facets', { p_deployment_ids: ['d1', 'd2'] }],
      ['order', 'facet'], ['order', 'value'], ['range', 0, PAGE_SIZE - 1],
    ]])
  })

  it('splits a long selection into chunks and merges their counts', async () => {
    const ids = Array.from({ length: IDS_PER_CALL + 1 }, (_, i) => `d${i}`)
    const { db, calls } = fakeDb(chunkIds => [row('scientific_name', 'Mus musculus', chunkIds.length)])
    const out = await fetchGridFacets(db, ids)
    expect(calls.map(c => (c[0][2] as { p_deployment_ids: string[] }).p_deployment_ids.length)).toEqual([IDS_PER_CALL, 1])
    expect(out.species).toEqual([{ value: 'Mus musculus', n: IDS_PER_CALL + 1 }])
  })

  it('pages past 1,000 rows', async () => {
    const rows = Array.from({ length: PAGE_SIZE + 2 }, (_, i) => row('scientific_name', `s${String(i).padStart(4, '0')}`, 1))
    const { db, calls } = fakeDb(() => rows)
    const out = await fetchGridFacets(db, ['d1'])
    expect(out.species).toHaveLength(PAGE_SIZE + 2)
    expect(calls).toHaveLength(2)
    expect(calls[1]).toContainEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
  })

  it('asks nothing for no deployments', async () => {
    const { db, calls } = fakeDb(() => [])
    expect(await fetchGridFacets(db, [])).toEqual({ species: [], models: [], annotators: [] })
    expect(calls).toHaveLength(0)
  })

  it('throws the error a call returns', async () => {
    const db = {
      rpc: () => {
        const chain = { order: () => chain, range: () => Promise.resolve({ data: null, error: { message: 'permission denied' } }) }
        return chain
      },
    } as unknown as SupabaseClient
    await expect(fetchGridFacets(db, ['d1'])).rejects.toThrow('permission denied')
  })
})
