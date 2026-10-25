import { describe, expect, it } from 'vitest'
import { boundsOf, spreadOffsets } from './mapMarkers'

const p = (id: string, latitude: number, longitude: number) => ({ id, latitude, longitude })

describe('boundsOf', () => {
  it('frames every point', () => {
    expect(boundsOf([p('a', -41, 174), p('b', -36.8, 175.1), p('c', -45, 170)])).toEqual({
      south: -45, west: 170, north: -36.8, east: 175.1,
    })
  })

  it('is null with no points', () => {
    expect(boundsOf([])).toBeNull()
  })
})

describe('spreadOffsets', () => {
  it('leaves a lone site where it is', () => {
    expect(spreadOffsets([p('a', -41, 174), p('b', -41.1, 174)]).size).toBe(0)
  })

  it('fans out deployments at the same site', () => {
    const offsets = spreadOffsets([p('a', -41, 174), p('b', -41.000001, 174), p('c', -42, 170)])
    expect([...offsets.keys()]).toEqual(['a', 'b'])
    const [ax, ay] = offsets.get('a')!
    const [bx, by] = offsets.get('b')!
    // Opposite sides of the site, at least the minimum radius away.
    expect(Math.hypot(ax, ay)).toBeGreaterThanOrEqual(15)
    expect(Math.hypot(ax - bx, ay - by)).toBeGreaterThanOrEqual(30)
  })

  it('widens the circle for a crowded site so neighbours do not overlap', () => {
    const many = Array.from({ length: 20 }, (_, i) => p(`d${i}`, -41, 174))
    const offsets = spreadOffsets(many, 26)
    const [x0, y0] = offsets.get('d0')!
    const [x1, y1] = offsets.get('d1')!
    expect(Math.hypot(x0 - x1, y0 - y1)).toBeGreaterThanOrEqual(24)
  })
})
