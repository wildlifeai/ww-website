import { afterEach, describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  clearRecentSpecies, fetchProjectSpecies, labelledSpecies, recentSpecies, rememberSpecies, seedMatches,
  subscribeRecentSpecies, PROJECTS_PER_REQUEST, type ProjectSpeciesRow, type SpeciesOption,
} from './speciesSuggestions'
import { PAGE_SIZE } from './liveObservations'

const rat: SpeciesOption = { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat' }
const mouse: SpeciesOption = { taxon_id: 't-mouse', scientific_name: 'Mus musculus', vernacular_name: 'House mouse' }
const stoat: SpeciesOption = { taxon_id: 't-stoat', scientific_name: 'Mustela erminea', vernacular_name: 'Stoat' }

afterEach(() => clearRecentSpecies())

describe('recent picks', () => {
  it('keeps the newest first without repeats, and tells subscribers', () => {
    let told = 0
    const stop = subscribeRecentSpecies(() => { told += 1 })
    rememberSpecies(rat); rememberSpecies(mouse); rememberSpecies(rat)
    stop()
    expect(recentSpecies()).toEqual([rat, mouse])
    expect(told).toBe(3)
  })

  it('keeps six', () => {
    for (let i = 0; i < 8; i++) rememberSpecies({ taxon_id: `t${i}`, scientific_name: `Species ${i}`, vernacular_name: null })
    expect(recentSpecies().map(s => s.taxon_id)).toEqual(['t7', 't6', 't5', 't4', 't3', 't2'])
  })
})

describe('labelledSpecies', () => {
  it('ranks the species by how often they are labelled and skips rows without a taxon', () => {
    const rows = [
      { taxon_id: 't-mouse', scientific_name: 'Mus musculus', vernacular_name: 'House mouse' },
      { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat' },
      { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat' },
      { taxon_id: null, scientific_name: 'Felis catus', vernacular_name: 'Cat' },
    ]
    expect(labelledSpecies(rows)).toEqual([rat, mouse])
  })

  it('adds up project_species uses across projects and names, filling a missing vernacular name', () => {
    const rows: ProjectSpeciesRow[] = [
      { taxon_id: 't-mouse', scientific_name: 'Mus musculus', vernacular_name: 'House mouse', uses: 5 },
      { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: null, uses: 3 },
      { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat', uses: 4 },
      { taxon_id: 't-stoat', scientific_name: 'Mustela erminea', vernacular_name: 'Stoat', uses: 1 },
      { taxon_id: null, scientific_name: 'Felis catus', vernacular_name: 'Cat', uses: 50 },
    ]
    expect(labelledSpecies(rows, Infinity)).toEqual([rat, mouse, stoat])
  })
})

describe('seedMatches', () => {
  it('lists recent picks before labelled species, once each', () => {
    expect(seedMatches('', [mouse], [rat, mouse, stoat])).toEqual([mouse, rat, stoat])
  })

  it('matches either name, ignoring case', () => {
    expect(seedMatches('MUS', [], [rat, mouse, stoat])).toEqual([mouse, stoat])
    expect(seedMatches('black', [], [rat, mouse])).toEqual([rat])
  })

  it('puts recent picks before the project species, which keep their order by uses', () => {
    const project = labelledSpecies([
      { taxon_id: 't-stoat', scientific_name: 'Mustela erminea', vernacular_name: 'Stoat', uses: 9 },
      { taxon_id: 't-rat', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat', uses: 7 },
      { taxon_id: 't-mouse', scientific_name: 'Mus musculus', vernacular_name: 'House mouse', uses: 2 },
    ], Infinity)
    expect(seedMatches('', [mouse], project)).toEqual([mouse, stoat, rat])
    expect(seedMatches('rat', [], project)).toEqual([rat])
  })
})

type Call = [string, ...unknown[]]

/** A client that records each query's calls and answers each `range` with `answer`. */
function fakeDb(answer: (calls: Call[]) => { data: unknown[] | null; error: { message: string } | null }) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'not', 'order']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => { calls.push(['range', ...args]); return Promise.resolve(answer(calls)) }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const row = (i: number): ProjectSpeciesRow => ({ taxon_id: `t${i}`, scientific_name: `Species ${i}`, vernacular_name: null, uses: 1 })

describe('fetchProjectSpecies', () => {
  it('reads the taxon rows of the projects from project_species in one request', async () => {
    const { db, queries } = fakeDb(() => ({ data: [row(1)], error: null }))
    expect(await fetchProjectSpecies(db, ['p1', 'p2'])).toEqual([row(1)])
    expect(queries).toEqual([[
      ['from', 'project_species'],
      ['select', 'taxon_id, scientific_name, vernacular_name, uses'],
      ['in', 'project_id', ['p1', 'p2']],
      ['not', 'taxon_id', 'is', null],
      ['order', 'project_id'], ['order', 'taxon_id'], ['order', 'scientific_name'], ['order', 'vernacular_name'],
      ['range', 0, PAGE_SIZE - 1],
    ]])
  })

  it('asks nothing for no projects', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchProjectSpecies(db, [])).toEqual([])
    expect(queries).toHaveLength(0)
  })

  it('sends long project lists in runs and pages past 1,000 rows', async () => {
    const ids = Array.from({ length: PROJECTS_PER_REQUEST + 1 }, (_, i) => `p${i}`)
    const { db, queries } = fakeDb(calls => {
      const [, from] = calls.find(c => c[0] === 'range') as [string, number]
      const many = (calls.find(c => c[0] === 'in') as [string, string, string[]])[2].length > 1
      return { data: many && from === 0 ? Array.from({ length: PAGE_SIZE }, (_, i) => row(i)) : [row(-1)], error: null }
    })
    const rows = await fetchProjectSpecies(db, ids)
    expect(queries).toHaveLength(3)
    expect(rows).toHaveLength(PAGE_SIZE + 2)
  })

  it('throws the database error', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'relation "project_species" does not exist' } }))
    await expect(fetchProjectSpecies(db, ['p1'])).rejects.toThrow('project_species')
  })
})
