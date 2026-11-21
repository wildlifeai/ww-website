import { describe, expect, it } from 'vitest'

import { boxCropLayout, boxOf, paddedBox } from './boxCrop'

describe('on-screen crop of a box', () => {
  it('pads the box and keeps it inside the photo', () => {
    const b = paddedBox({ x: 0.4, y: 0.4, w: 0.2, h: 0.2 })
    expect(b.x).toBeCloseTo(0.38, 9)
    expect(b.y).toBeCloseTo(0.38, 9)
    expect(b.w).toBeCloseTo(0.24, 9)
    expect(b.h).toBeCloseTo(0.24, 9)
    const edge = paddedBox({ x: 0, y: 0.9, w: 0.5, h: 0.1 })
    expect(edge.x).toBe(0)
    expect(edge.y + edge.h).toBeCloseTo(1, 9)
  })

  it('fits a wide box to the card width', () => {
    // A 4:3 photo, a box half the photo wide and a tenth high, in a 4:3 card.
    const { widthPct, cx, cy } = boxCropLayout({ x: 0.25, y: 0.45, w: 0.5, h: 0.1 }, 4 / 3, 4 / 3)
    expect(widthPct).toBeCloseTo(100 / 0.6, 5)
    expect(cx).toBeCloseTo(0.5, 5)
    expect(cy).toBeCloseTo(0.5, 5)
  })

  it('fits a tall box to the card height without stretching', () => {
    // The padded box is 0.12 wide and 0.6 high; its height fills the card.
    const { widthPct } = boxCropLayout({ x: 0.45, y: 0.25, w: 0.1, h: 0.5 }, 4 / 3, 4 / 3)
    expect(widthPct).toBeCloseTo(100 / 0.6, 5)
    // Rendered box height = card width x widthPct/100 / photoAspect x 0.6 = the card height.
    expect(((widthPct / 100) / (4 / 3)) * 0.6 * (4 / 3)).toBeCloseTo(1, 5)
  })

  it('reads a box only when all four parts are there', () => {
    expect(boxOf({ bbox_x: 0.1, bbox_y: 0.2, bbox_w: 0.3, bbox_h: 0.4 })).toEqual({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 })
    expect(boxOf({ bbox_x: 0.1, bbox_y: 0.2, bbox_w: null, bbox_h: 0.4 })).toBeNull()
    expect(boxOf({ bbox_x: 0.1, bbox_y: 0.2, bbox_w: 0, bbox_h: 0.4 })).toBeNull()
  })
})
