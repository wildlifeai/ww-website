import { describe, expect, it } from 'vitest'
import { fileStamp, frameFileName, jpegBytes, logFileName } from './liveViewFiles'

const AT = new Date(2026, 9, 26, 9, 5, 7)

describe('live view file names', () => {
  it('stamp the local time so a bench run sorts in order', () => {
    expect(fileStamp(AT)).toBe('2026-10-26T090507')
  })

  it('name a frame by its time and count, the gridded copy as a PNG', () => {
    expect(frameFileName(AT, 42, false)).toBe('ww500-frame-2026-10-26T090507-42.jpg')
    expect(frameFileName(AT, 42, true)).toBe('ww500-frame-2026-10-26T090507-42-grid.png')
  })

  it('name the session log', () => {
    expect(logFileName(AT)).toBe('ww500-live-view-2026-10-26T090507.txt')
  })
})

describe('jpegBytes', () => {
  it('decodes the frame base64 to its bytes', () => {
    expect([...jpegBytes('/9j/4A==')]).toEqual([0xff, 0xd8, 0xff, 0xe0])
  })
})
