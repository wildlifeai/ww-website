import { describe, expect, it } from 'vitest'
import { neighbourPreviewUrls } from './viewerPrefetch'

const photo = (n: number) => ({ media_assets: { preview_url: `p${n}`, thumbnail_url: `t${n}` } })
const list = [0, 1, 2, 3, 4, 5, 6].map(photo)

describe('neighbourPreviewUrls', () => {
  it('takes the two photos either side, nearest first and next before previous', () => {
    expect(neighbourPreviewUrls(list, 3)).toEqual(['p4', 'p2', 'p5', 'p1'])
  })

  it('stops at the ends of the list', () => {
    expect(neighbourPreviewUrls(list, 0)).toEqual(['p1', 'p2'])
    expect(neighbourPreviewUrls(list, 6)).toEqual(['p5', 'p4'])
  })

  it('falls back to the thumbnail, reads an array embed, and skips a photo with neither', () => {
    const mixed = [
      { media_assets: { preview_url: null, thumbnail_url: 't0' } },
      { media_assets: [{ preview_url: 'p1', thumbnail_url: 't1' }] },
      { media_assets: null },
    ]
    expect(neighbourPreviewUrls(mixed, 1)).toEqual(['t0'])
  })

  it('loads nothing for a photo that is not in the list', () => {
    expect(neighbourPreviewUrls(list, -1)).toEqual([])
  })
})
