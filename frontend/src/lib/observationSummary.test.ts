import { describe, expect, it } from 'vitest'
import {
  capRows, dayKey, detectionsPerDay, OTHER_SPECIES, perDayCsv, summariseObservations, type SummaryObservation,
} from './observationSummary'

const ob = (deployment_id: string, scientific_name: string | null, observation_type: string | null, captured_at: string | null = '2026-09-01T10:00:00Z'): SummaryObservation =>
  ({ deployment_id, scientific_name, observation_type, captured_at })

describe('summariseObservations', () => {
  const obs = [
    ob('d1', 'Rattus rattus', 'animal'),
    ob('d1', 'Rattus rattus', 'animal'),
    ob('d1', '(unidentified)', 'animal'),
    ob('d2', null, 'blank'),
    ob('d3', 'Homo sapiens', 'human'),
    ob('d3', null, null),
  ]

  it('counts once into each counter', () => {
    const s = summariseObservations(obs)
    expect(s.total).toBe(6)
    expect(s.uniqueSpecies).toBe(2)
    expect(s.animals).toBe(3)
    // d2 has only a blank observation, so it is not a deployment with detections.
    expect(s.deploymentsWithDetections).toBe(2)
  })

  it('gives each chart its own small table, most first', () => {
    const s = summariseObservations(obs, new Map([['d1', 'Hut · 9 Jun 2026']]))
    expect(s.species.map(r => [r.label, r.count])).toEqual([['Rattus rattus', 2], ['(unidentified)', 1], ['Homo sapiens', 1]])
    expect(s.types.map(r => [r.label, r.count])).toEqual([['animal', 3], ['blank', 1], ['human', 1], ['unknown', 1]])
    expect(s.deployments.map(r => [r.key, r.label, r.count])).toEqual([
      ['d1', 'Hut · 9 Jun 2026', 3], ['d3', 'd3', 2], ['d2', 'd2', 1],
    ])
  })

  it('is empty for no observations', () => {
    const s = summariseObservations([])
    expect(s).toMatchObject({ total: 0, uniqueSpecies: 0, deploymentsWithDetections: 0, animals: 0, species: [] })
  })
})

describe('capRows', () => {
  it('keeps the first rows and says how many more there are', () => {
    expect(capRows([1, 2, 3, 4], 3)).toEqual({ shown: [1, 2, 3], more: 1 })
    expect(capRows([1], 15)).toEqual({ shown: [1], more: 0 })
  })
})

describe('dayKey', () => {
  it('takes the day in the deployment zone', () => {
    expect(dayKey('2026-06-08T20:00:00Z', 'Pacific/Auckland')).toBe('2026-06-09')
    expect(dayKey('2026-06-08T20:00:00Z', 'UTC')).toBe('2026-06-08')
  })

  it('falls back for an unknown zone and rejects a bad date', () => {
    expect(dayKey('2026-06-08T12:00:00Z', 'Not/AZone')).toMatch(/^2026-06-0[89]$/)
    expect(dayKey('not a date', 'UTC')).toBeNull()
  })
})

describe('detectionsPerDay', () => {
  it('counts named observations by day and species', () => {
    const { rows, species } = detectionsPerDay([
      ob('d1', 'Rattus rattus', 'animal', '2026-09-01T01:00:00Z'),
      ob('d1', 'Rattus rattus', 'animal', '2026-09-01T02:00:00Z'),
      ob('d1', 'Mus musculus', 'animal', '2026-09-02T02:00:00Z'),
      ob('d1', null, 'blank', '2026-09-02T02:00:00Z'),
    ], 'UTC')
    expect(species).toEqual(['Rattus rattus', 'Mus musculus'])
    expect(rows).toEqual([
      { day: '2026-09-01', species: 'Rattus rattus', count: 2 },
      { day: '2026-09-02', species: 'Mus musculus', count: 1 },
    ])
  })

  it('takes the capture day in the deployment zone', () => {
    // 23:30 UTC on 1 Sep is 11:30 on 2 Sep in Auckland (NZST, +12).
    const late = [ob('d1', 'Rattus rattus', 'animal', '2026-09-01T23:30:00Z')]
    expect(detectionsPerDay(late, 'Pacific/Auckland').rows).toEqual([{ day: '2026-09-02', species: 'Rattus rattus', count: 1 }])
    expect(detectionsPerDay(late, 'UTC').rows).toEqual([{ day: '2026-09-01', species: 'Rattus rattus', count: 1 }])
  })

  it('leaves out and counts a photo with no capture time', () => {
    const { rows, species, undated } = detectionsPerDay([
      ob('d1', 'Rattus rattus', 'animal', '2026-09-01T01:00:00Z'),
      ob('d1', 'Mus musculus', 'animal', null),
      ob('d1', null, 'blank', null),
    ], 'UTC')
    expect(rows).toEqual([{ day: '2026-09-01', species: 'Rattus rattus', count: 1 }])
    // The legend names only species the chart draws.
    expect(species).toEqual(['Rattus rattus'])
    expect(undated).toBe(1)
  })

  it('dates a re-labelled photo by its capture time, not its label time', () => {
    const relabelled = { ...ob('d1', 'Rattus rattus', 'animal', '2026-09-01T10:00:00Z'), created_at: '2026-10-20T03:00:00Z' }
    const { rows, undated } = detectionsPerDay([relabelled], 'UTC')
    expect(rows).toEqual([{ day: '2026-09-01', species: 'Rattus rattus', count: 1 }])
    expect(undated).toBe(0)
  })

  it('sums the species past the cap into Other species', () => {
    const many = ['A a', 'B b', 'C c'].flatMap((sp, i) => Array.from({ length: 3 - i }, () => ob('d1', sp, 'animal')))
    const { rows, species } = detectionsPerDay(many, 'UTC', 1)
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
      ob('d1', 'Rattus rattus', 'animal', '2026-09-02T01:00:00Z'),
      ob('d1', 'Rattus rattus', 'animal', '2026-09-01T01:00:00Z'),
      ob('d1', 'Mus musculus', 'animal', '2026-09-02T02:00:00Z'),
    ], 'UTC')
    expect(perDayCsv(rows, ['Rattus rattus', 'Mus musculus', 'A, "b"'])).toBe([
      'date,Rattus rattus,Mus musculus,"A, ""b"""',
      '2026-09-01,1,0,0',
      '2026-09-02,1,1,0',
    ].join('\n'))
  })
})
