import { afterEach, describe, expect, it } from 'vitest'
import {
  clearRecentSpecies, labelledSpecies, recentSpecies, rememberSpecies, seedMatches, subscribeRecentSpecies,
  type SpeciesOption,
} from './speciesSuggestions'

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
})

describe('seedMatches', () => {
  it('lists recent picks before labelled species, once each', () => {
    expect(seedMatches('', [mouse], [rat, mouse, stoat])).toEqual([mouse, rat, stoat])
  })

  it('matches either name, ignoring case', () => {
    expect(seedMatches('MUS', [], [rat, mouse, stoat])).toEqual([mouse, stoat])
    expect(seedMatches('black', [], [rat, mouse])).toEqual([rat])
  })
})
