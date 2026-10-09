import { describe, expect, it } from 'vitest'
import { displayImageUrl, mediaImageUrl } from './mediaImageUrl'

const both = { thumbnail_url: 'https://cdn/t.jpg', preview_url: 'https://cdn/p.jpg' }
const drive = 'gdrive://folder/IMG_0001.JPG'

describe('mediaImageUrl', () => {
  it('uses the thumbnail for a card and the preview for the viewer', () => {
    const m = { file_path: drive, media_assets: both }
    expect(mediaImageUrl(m, 'thumb')).toBe('https://cdn/t.jpg')
    expect(mediaImageUrl(m, 'full')).toBe('https://cdn/p.jpg')
  })

  it('falls back to the other rendition and reads an array embed', () => {
    expect(mediaImageUrl({ file_path: drive, media_assets: { thumbnail_url: null, preview_url: 'p' } }, 'thumb')).toBe('p')
    expect(mediaImageUrl({ file_path: drive, media_assets: [{ thumbnail_url: 't', preview_url: null }] }, 'full')).toBe('t')
  })

  it('prefers a rendition over the local upload preview, and the local preview over the original', () => {
    expect(mediaImageUrl({ file_path: drive, media_assets: both }, 'thumb', 'blob:local')).toBe('https://cdn/t.jpg')
    expect(mediaImageUrl({ file_path: 'https://host/a.jpg', media_assets: null }, 'thumb', 'blob:local')).toBe('blob:local')
  })

  it('keeps a public http(s) original', () => {
    expect(mediaImageUrl({ file_path: 'https://host/a.jpg', media_assets: null }, 'full')).toBe('https://host/a.jpg')
    expect(mediaImageUrl({ file_path: 'http://host/a.jpg', media_assets: [] }, 'thumb')).toBe('http://host/a.jpg')
  })

  it('returns null rather than the auth-gated proxy for a Drive original with no rendition', () => {
    expect(mediaImageUrl({ file_path: drive, media_assets: null }, 'full')).toBeNull()
    expect(mediaImageUrl({ file_path: drive, media_assets: { thumbnail_url: null, preview_url: null } }, 'thumb')).toBeNull()
    expect(mediaImageUrl({ file_path: '', media_assets: null }, 'thumb')).toBeNull()
  })
})

describe('displayImageUrl', () => {
  it('is mediaImageUrl with no local preview for a file this browser did not upload', () => {
    expect(displayImageUrl({ file_name: 'IMG_9.JPG', file_path: drive, media_assets: both }, 'full')).toBe('https://cdn/p.jpg')
    expect(displayImageUrl({ file_name: 'IMG_9.JPG', file_path: drive, media_assets: null }, 'thumb')).toBeNull()
  })
})
