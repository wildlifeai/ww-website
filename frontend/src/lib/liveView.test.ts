import { describe, expect, it } from 'vitest'
import {
  CHAR_DELAY_MS, HM0360_MAX_INTERVAL_MS, LineSplitter, captureIntervalMs, cleanLine, motionCells,
  parseFrameLine, replyExpected, typeSlowly, validOpValue, wouldWake,
} from './liveView'

// A tiny base64 JPEG header: valid base64, a multiple of 4 characters.
const JPEG_B64 = '/9j/4AAQSkZJRg=='

/** A frame line as preview.c prints it at e3c4d18a: CR first, then the JSON, LF last. */
function frameLine({ count = 1, md, mdstat, image = JPEG_B64 }: {
  count?: number
  md?: string
  mdstat?: { int: number; ctrl: number; ctrl1: number; thA: number; thB: number }
  image?: string
} = {}): string {
  return `\r{"type": 1, "name": "INVOKE", "code": 0, "data": {"count": ${count}, "resolution": [640, 480], "boxes": [], `
    + (md !== undefined ? `"md": "${md}", ` : '')
    + (mdstat ? `"mdstat": {"int": ${mdstat.int}, "ctrl": ${mdstat.ctrl}, "ctrl1": ${mdstat.ctrl1}, "thA": ${mdstat.thA}, "thB": ${mdstat.thB}}, ` : '')
    + `"image": "${image}"}}\n`
}

/** 32 motion bytes with the given bits set (bit b = byte b >> 3, LSB first). */
function mdBytes(bits: number[]): Uint8Array {
  const md = new Uint8Array(32)
  for (const b of bits) md[b >> 3] |= 1 << (b & 7)
  return md
}

const mdHex = (bits: number[]) => [...mdBytes(bits)].map(x => x.toString(16).padStart(2, '0')).join('')

const bytes = (s: string) => new TextEncoder().encode(s)
const str = (b: Uint8Array) => new TextDecoder().decode(b)

describe('typeSlowly', () => {
  it('types one byte at a time, 30 ms apart', async () => {
    const written: string[] = []
    const sleeps: number[] = []
    await typeSlowly('ver\r\n', async b => { written.push(str(b)) }, async ms => { sleeps.push(ms) })
    expect(written).toEqual(['v', 'e', 'r', '\r', '\n'])
    expect(sleeps).toEqual([CHAR_DELAY_MS, CHAR_DELAY_MS, CHAR_DELAY_MS, CHAR_DELAY_MS, CHAR_DELAY_MS])
  })
})

describe('LineSplitter', () => {
  it('splits on LF, joining a line that spans chunks', () => {
    const split = new LineSplitter()
    expect(split.push(bytes('OpParam 8 = 10')).map(str)).toEqual([])
    expect(split.push(bytes('00\r\nPreview off.\r\nSet Op')).map(str)).toEqual(['OpParam 8 = 1000\r', 'Preview off.\r'])
    expect(split.push(bytes('Param 7 = 0\n')).map(str)).toEqual(['Set OpParam 7 = 0'])
  })

  it('drops a run with no newline longer than half a megabyte', () => {
    const split = new LineSplitter()
    split.push(new Uint8Array(400 * 1024).fill(65))
    split.push(new Uint8Array(200 * 1024).fill(65))
    expect(split.overflows).toBe(1)
    expect(split.push(bytes('tail\nnext\n')).map(str)).toEqual(['tail', 'next'])
  })
})

describe('cleanLine', () => {
  it('strips colour codes and the surrounding CR, LF and spaces', () => {
    expect(cleanLine('\x1b[32mOpParam 11 = 500\x1b[0m\r')).toBe('OpParam 11 = 500')
    expect(cleanLine(' \r\n ')).toBe('')
  })
})

describe('parseFrameLine', () => {
  it('reads the picture, the motion bytes and the wake registers', () => {
    const md = mdHex([0, 9])
    const frame = parseFrameLine(cleanLine(frameLine({ count: 7, md, mdstat: { int: 8, ctrl: 1, ctrl1: 2, thA: 4, thB: 5 } })))
    expect(frame).not.toBe('damaged')
    if (!frame || frame === 'damaged') return
    expect(frame).toMatchObject({ count: 7, width: 640, height: 480, image: JPEG_B64, mdstat: { int: 8, ctrl: 1, ctrl1: 2, thA: 4, thB: 5 } })
    expect([...(frame.md ?? [])].slice(0, 2)).toEqual([0x01, 0x02])
  })

  it('has no motion grid without "md", and no registers without "mdstat"', () => {
    const frame = parseFrameLine(frameLine())
    expect(frame).toMatchObject({ md: null, mdstat: null, image: JPEG_B64 })
    expect(parseFrameLine(frameLine({ md: 'abc' }))).toMatchObject({ md: null })
  })

  it('finds the frame after a prompt on the same line', () => {
    expect(parseFrameLine(`cmd> ${frameLine()}`)).toMatchObject({ image: JPEG_B64 })
  })

  it('calls a frame damaged when another print lands inside it', () => {
    expect(parseFrameLine(frameLine({ image: '/9j/4AAQ[IF] sent MD to BLE SkZJRg==' }))).toBe('damaged')
    expect(parseFrameLine(frameLine().slice(0, 90))).toBe('damaged')
  })

  it('ignores ordinary console lines', () => {
    expect(parseFrameLine('OpParam 33 = 4')).toBeNull()
  })
})

describe('motionCells', () => {
  it('turns the console grid 180 degrees into picture order', () => {
    // Bit 0 is console row 0, column 0: the picture's bottom-right cell.
    expect(motionCells(mdBytes([0]))).toEqual([255])
    // Bit 255 is the picture's top-left; bit 16 (console row 1, column 0) is row 14, column 15.
    expect(motionCells(mdBytes([255, 16]))).toEqual([0, 14 * 16 + 15])
    expect(motionCells(new Uint8Array(32))).toEqual([])
  })
})

describe('wouldWake', () => {
  it('wakes on more cells than the threshold, not as many', () => {
    expect(wouldWake(4, 4)).toBe(false)
    expect(wouldWake(5, 4)).toBe(true)
  })
})

describe('captureIntervalMs', () => {
  it('follows op 11 on the HM0360, within what its sleep register holds', () => {
    expect(HM0360_MAX_INTERVAL_MS).toBe(1997)
    expect(captureIntervalMs('HM0360', 500)).toBe(500)
    expect(captureIntervalMs('HM0360', 5000)).toBe(1997)
    expect(captureIntervalMs('HM0360', 5)).toBe(20)
    expect(captureIntervalMs('HM0360', 0)).toBe(1000)
    expect(captureIntervalMs('HM0360', null)).toBe(1000)
  })

  it('uses 100 ms on the RP3 and 500 ms until the camera is known', () => {
    expect(captureIntervalMs('RP3', 500)).toBe(100)
    expect(captureIntervalMs(null, null)).toBe(500)
  })
})

describe('op values and replies', () => {
  it('keeps each motion op in its range', () => {
    expect(validOpValue(17, 3)).toBe(true)
    expect(validOpValue(17, 4)).toBe(false)
    expect(validOpValue(33, 0)).toBe(false)
    expect(validOpValue(33, 255)).toBe(true)
    expect(validOpValue(11, 0)).toBe(true)
    expect(validOpValue(11, 1998)).toBe(false)
    expect(validOpValue(11, 2.5)).toBe(false)
  })

  it('knows the reply to each command it sends', () => {
    expect(replyExpected('getop 33')).toEqual(['OpParam 33 ='])
    expect(replyExpected('setop 33 6')).toEqual(['Set OpParam 33 =', 'valid value for op 33.'])
    expect(replyExpected('camera')).toEqual(['HM0360', 'RP3', 'RP2'])
  })
})
