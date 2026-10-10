import { describe, expect, it } from 'vitest'
import { deploymentFilterOptions, matchOptions, startDateLabel } from './deploymentFilterOptions'

const P1 = '11111111-0000-0000-0000-000000000000'
const P2 = '22222222-0000-0000-0000-000000000000'
const dep = (id: string, location_name: string | null, deployment_start: string | null, project_id = P1, timezone: string | null = 'UTC') =>
  ({ id, project_id, location_name, deployment_start, timezone })

describe('startDateLabel', () => {
  it('formats the start in the deployment zone', () => {
    // 20:00 UTC on 8 June is 9 June in Auckland.
    expect(startDateLabel('2026-06-08T20:00:00Z', 'Pacific/Auckland')).toBe('9 Jun 2026')
    expect(startDateLabel('2026-06-08T20:00:00Z', 'UTC')).toBe('8 Jun 2026')
  })

  it('is null for a missing or bad date and survives an unknown zone', () => {
    expect(startDateLabel(null)).toBeNull()
    expect(startDateLabel('not a date')).toBeNull()
    expect(startDateLabel('2026-06-08T20:00:00Z', 'Not/AZone')).toMatch(/Jun 2026$/)
  })
})

describe('deploymentFilterOptions', () => {
  it('tells apart deployments that share a location name by their start date', () => {
    const opts = deploymentFilterOptions([
      dep('aaaaaaaa-1', 'User Location', '2026-06-16T08:00:00Z'),
      dep('bbbbbbbb-2', 'User Location', '2026-06-08T08:00:00Z'),
    ])
    expect(opts.map(o => o.label)).toEqual(['User Location · 16 Jun 2026', 'User Location · 8 Jun 2026'])
  })

  it('sorts by location name, then newest first', () => {
    const opts = deploymentFilterOptions([
      dep('c', 'kiwi creek', '2026-01-01T00:00:00Z'),
      dep('a', 'Beach', '2026-01-01T00:00:00Z'),
      dep('b', 'Kiwi Creek', '2026-03-01T00:00:00Z'),
    ])
    expect(opts.map(o => o.value)).toEqual(['a', 'b', 'c'])
  })

  it('adds the project only when the deployments span several projects', () => {
    const names = new Map([[P1, 'Sunset'], [P2, 'Ridge']])
    expect(deploymentFilterOptions([dep('a', 'Hut', null)], names)[0].label).toBe('Hut')
    const opts = deploymentFilterOptions([dep('a', 'Hut', null, P1), dep('b', 'Hut', null, P2)], names)
    expect(opts.map(o => o.label).sort()).toEqual(['Hut · Ridge', 'Hut · Sunset'])
  })

  it('falls back to the id prefix for a missing name and for labels that still collide', () => {
    const opts = deploymentFilterOptions([
      dep('12345678-aaaa', null, null),
      dep('aaaaaaaa-x', 'Hut', '2026-06-08T08:00:00Z'),
      dep('bbbbbbbb-y', 'Hut', '2026-06-08T09:00:00Z'),
    ])
    expect(opts.map(o => o.label)).toEqual([
      '12345678',
      'Hut · 8 Jun 2026 · bbbbbbbb',
      'Hut · 8 Jun 2026 · aaaaaaaa',
    ])
  })

  it('keeps every deployment it is given', () => {
    const many = Array.from({ length: 1500 }, (_, i) => dep(`id-${i}`, `Site ${i}`, null))
    expect(deploymentFilterOptions(many)).toHaveLength(1500)
  })
})

describe('matchOptions', () => {
  const opts = [
    { value: 'a', label: 'User Location · 16 Jun 2026' },
    { value: 'b', label: 'User Location · 8 Jun 2026' },
    { value: 'c', label: 'Kiwi Creek · 1 Mar 2026' },
  ]

  it('keeps everything for an empty or blank query', () => {
    expect(matchOptions(opts, '')).toBe(opts)
    expect(matchOptions(opts, '   ')).toBe(opts)
  })

  it('matches every word, in any order, ignoring case', () => {
    expect(matchOptions(opts, 'user').map(o => o.value)).toEqual(['a', 'b'])
    expect(matchOptions(opts, 'JUN 16').map(o => o.value)).toEqual(['a'])
    expect(matchOptions(opts, 'creek kiwi').map(o => o.value)).toEqual(['c'])
    expect(matchOptions(opts, 'nowhere')).toEqual([])
  })
})
