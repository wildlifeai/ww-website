import { describe, expect, it } from 'vitest'
import {
  capRows, detectionsPerDay, filterCounts, OTHER_SPECIES, perDayCsv, summariseObservations,
  type DailyCount, type ObservationCount,
} from './observationSummary'

const c = (deployment_id: string, scientific_name: string | null, observation_type: string | null, n = 1): ObservationCount =>
  ({ deployment_id, scientific_name, observation_type, n })

const day = (d: string | null, scientific_name: string | null, n = 1): DailyCount =>
  ({ deployment_id: 'd1', day: d, scientific_name, n })

describe('summariseObservations', () => {
  const counts = [
    c('d1', 'Rattus rattus', 'animal', 2),
    c('d1', '(unidentified)', 'animal'),
    c('d2', null, 'blank'),
    c('d3', 'Homo sapiens', 'human'),
    c('d3', null, null),
    // An empty type is 'unknown' too, and an empty name is no species.
    c('d1', '', ''),
  ]

  it('sums the counts into each counter', () => {
    const s = summariseObservations(counts)
    expect(s.total).toBe(7)
    expect(s.uniqueSpecies).toBe(2)
    expect(s.animals).toBe(3)
    // d2 has only a blank observation, so it is not a deployment with detections.
    expect(s.deploymentsWithDetections).toBe(2)
  })

  it('gives each chart its own small table, most first', () => {
    const s = summariseObservations(counts, new Map([['d1', 'Hut · 9 Jun 2026']]))
    expect(s.species.map(r => [r.label, r.count])).toEqual([['Rattus rattus', 2], ['(unidentified)', 1], ['Homo sapiens', 1]])
    expect(s.types.map(r => [r.label, r.count])).toEqual([['animal', 3], ['unknown', 2], ['blank', 1], ['human', 1]])
    expect(s.deployments.map(r => [r.key, r.label, r.count])).toEqual([
      ['d1', 'Hut · 9 Jun 2026', 4], ['d3', 'd3', 2], ['d2', 'd2', 1],
    ])
  })

  it('is empty for no observations', () => {
    const s = summariseObservations([])
    expect(s).toMatchObject({ total: 0, uniqueSpecies: 0, deploymentsWithDetections: 0, animals: 0, species: [] })
  })
})

describe('filterCounts', () => {
  const counts = [c('d1', 'Rattus rattus', 'animal'), c('d2', 'Mus musculus', 'animal'), c('d2', null, 'blank')]

  it('keeps the chosen deployments and species', () => {
    expect(filterCounts(counts, ['d2'], [])).toEqual(counts.slice(1))
    expect(filterCounts(counts, [], ['Rattus rattus'])).toEqual([counts[0]])
    expect(filterCounts(counts, ['d1'], ['Mus musculus'])).toEqual([])
  })

  it('keeps everything with no filter', () => {
    expect(filterCounts(counts, [], [])).toBe(counts)
  })
})

describe('capRows', () => {
  it('keeps the first rows and says how many more there are', () => {
    expect(capRows([1, 2, 3, 4], 3)).toEqual({ shown: [1, 2, 3], more: 1 })
    expect(capRows([1], 15)).toEqual({ shown: [1], more: 0 })
  })
})

describe('detectionsPerDay', () => {
  it('sums named observations by day and species', () => {
    const { rows, species } = detectionsPerDay([
      day('2026-09-01', 'Rattus rattus', 2),
      day('2026-09-02', 'Mus musculus'),
      day('2026-09-02', '', 4),
    ])
    expect(species).toEqual(['Rattus rattus', 'Mus musculus'])
    expect(rows).toEqual([
      { day: '2026-09-01', species: 'Rattus rattus', count: 2 },
      { day: '2026-09-02', species: 'Mus musculus', count: 1 },
    ])
  })

  it('leaves out and counts the observations with no capture day', () => {
    const { rows, species, undated } = detectionsPerDay([
      day('2026-09-01', 'Rattus rattus'),
      day(null, 'Mus musculus', 3),
    ])
    expect(rows).toEqual([{ day: '2026-09-01', species: 'Rattus rattus', count: 1 }])
    // The legend names only species the chart draws.
    expect(species).toEqual(['Rattus rattus'])
    expect(undated).toBe(3)
  })

  it('sums the species past the cap into Other species', () => {
    const { rows, species } = detectionsPerDay([
      day('2026-09-01', 'A a', 3), day('2026-09-01', 'B b', 2), day('2026-09-01', 'C c', 1),
    ], 1)
    expect(species).toEqual(['A a', OTHER_SPECIES])
    expect(rows).toEqual([
      { day: '2026-09-01', species: 'A a', count: 3 },
      { day: '2026-09-01', species: OTHER_SPECIES, count: 3 },
    ])
  })
})

describe('perDayCsv', () => {
  it('writes one row per day and one column per species, zeros filled', () => {
    const { rows } = detectionsPerDay([
      day('2026-09-02', 'Rattus rattus'),
      day('2026-09-01', 'Rattus rattus'),
      day('2026-09-02', 'Mus musculus'),
    ])
    expect(perDayCsv(rows, ['Rattus rattus', 'Mus musculus', 'A, "b"'])).toBe([
      'date,Rattus rattus,Mus musculus,"A, ""b"""',
      '2026-09-01,1,0,0',
      '2026-09-02,1,1,0',
    ].join('\n'))
  })
})
