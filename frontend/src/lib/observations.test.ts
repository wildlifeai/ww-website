import { describe, expect, it } from 'vitest'
import { groupByBox } from './observations'

const box = (id: string, x: number | null, y = 0.2, w = 0.3, h = 0.4) => ({ id, bbox_x: x, bbox_y: y, bbox_w: w, bbox_h: h })

describe('groupByBox', () => {
  it('puts a detection and its per-crop classifier row on one box', () => {
    const speciesnet = box('speciesnet', 0.1)
    const bioclip = box('bioclip', 0.1)
    const other = box('other animal', 0.6)
    expect(groupByBox([speciesnet, other, bioclip]).map(g => g.map(o => o.id)))
      .toEqual([['speciesnet', 'bioclip'], ['other animal']])
  })

  it('treats float noise below 1e-4 as the same box', () => {
    expect(groupByBox([box('a', 0.1), box('b', 0.1 + 1e-6)])).toHaveLength(1)
  })

  it('leaves out rows without a full box', () => {
    expect(groupByBox([box('whole image', null), { id: 'partial', bbox_x: 0.1 }])).toEqual([])
  })
})
