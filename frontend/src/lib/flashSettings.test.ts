import { describe, expect, it } from 'vitest'
import { defaultWindow, describeUtc, formatHHMM, parseHHMM, windowFromLocal, windowToLocal } from './flashSettings'

const NZDT = 13 * 60 // Auckland in summer, UTC+13
const UTC = 0

describe('flashSettings', () => {
  it('parses and formats clock times', () => {
    expect(parseHHMM('19:30')).toBe(1170)
    expect(parseHHMM('24:00')).toBeNull()
    expect(parseHHMM('7pm')).toBeNull()
    expect(formatHHMM(1170)).toBe('19:30')
    expect(formatHHMM(1440 + 60)).toBe('01:00')
    expect(formatHHMM(-60)).toBe('23:00')
  })

  it('stores a local evening window as UTC minutes, wrapping past midnight', () => {
    // 19:00 to 06:00 in Auckland summer is 06:00 to 17:00 UTC.
    const w = windowFromLocal('19:00', '06:00', NZDT)!
    expect(w).toEqual({ startUtc: 6 * 60, minutes: 11 * 60 })
    expect(describeUtc(w)).toBe('06:00 to 17:00 UTC')
    expect(windowToLocal(w, NZDT)).toEqual({ start: '19:00', end: '06:00' })
  })

  it('keeps values inside the column ranges', () => {
    // A start that crosses midnight backwards in UTC still lands in 0..1439.
    expect(windowFromLocal('05:00', '07:00', NZDT)).toEqual({ startUtc: 16 * 60, minutes: 120 })
    // The same start and end means the whole day, 1440, not 0.
    expect(windowFromLocal('08:00', '08:00', UTC)).toEqual({ startUtc: 480, minutes: 1440 })
    expect(windowFromLocal('8', '09:00', UTC)).toBeNull()
  })

  it('defaults to 18:00 to 06:00 local', () => {
    expect(windowToLocal(defaultWindow(NZDT), NZDT)).toEqual({ start: '18:00', end: '06:00' })
  })
})
