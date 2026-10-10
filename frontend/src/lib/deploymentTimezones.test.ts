import { describe, expect, it, vi } from 'vitest'
import { FILL_BATCH, fillTimezones, missingTimezoneIds, withTimezones } from './deploymentTimezones'

describe('missingTimezoneIds', () => {
  it('asks about empty zones, not rows without coordinates or read without the column', () => {
    expect(missingTimezoneIds([
      { id: 'a', timezone: null, latitude: -41.2, longitude: 174.7 },
      { id: 'b', timezone: null },
      { id: 'c', timezone: 'Pacific/Auckland', latitude: -41.2, longitude: 174.7 },
      { id: 'd', timezone: null, latitude: null, longitude: null },
      { id: 'e', latitude: -41.2, longitude: 174.7 },
    ])).toEqual(['a', 'b'])
  })
})

describe('fillTimezones', () => {
  it('sends the ids in batches and merges the answers', async () => {
    const ids = Array.from({ length: FILL_BATCH + 1 }, (_, i) => `id-${i}`)
    const post = vi.fn()
      .mockResolvedValueOnce({ data: { 'id-0': 'Pacific/Auckland' } })
      .mockResolvedValueOnce({ data: { [`id-${FILL_BATCH}`]: 'Europe/London' } })
    expect(await fillTimezones(post, ids)).toEqual({ 'id-0': 'Pacific/Auckland', [`id-${FILL_BATCH}`]: 'Europe/London' })
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[0]).toEqual(['/api/deployments/fill-timezones', { deployment_ids: ids.slice(0, FILL_BATCH) }])
    expect(post.mock.calls[1][1]).toEqual({ deployment_ids: [`id-${FILL_BATCH}`] })
  })
})

describe('withTimezones', () => {
  it('fills only empty zones', () => {
    const rows = [{ id: 'a', timezone: null }, { id: 'b', timezone: 'UTC' }]
    expect(withTimezones(rows, { a: 'Pacific/Auckland', b: 'Europe/London' }))
      .toEqual([{ id: 'a', timezone: 'Pacific/Auckland' }, { id: 'b', timezone: 'UTC' }])
  })

  it('keeps the same array when nothing applies', () => {
    const rows = [{ id: 'a', timezone: null }]
    expect(withTimezones(rows, {})).toBe(rows)
  })
})
