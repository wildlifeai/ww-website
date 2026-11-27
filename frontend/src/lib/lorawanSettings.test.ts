import { describe, expect, it } from 'vitest'
import { formatPing, LORAWAN_PING_MINUTES, PING_CHOICES, pingChoices } from './lorawanSettings'

describe('check-in choices', () => {
  it('offers 4, 6, 8, 12 or 24 hours, inside the column range, with the default among them', () => {
    expect(PING_CHOICES).toEqual([240, 360, 480, 720, 1440])
    for (const m of PING_CHOICES) {
      expect(m).toBeGreaterThanOrEqual(LORAWAN_PING_MINUTES.min)
      expect(m).toBeLessThanOrEqual(LORAWAN_PING_MINUTES.max)
    }
    expect(PING_CHOICES).toContain(LORAWAN_PING_MINUTES.default)
  })

  it('keeps a stored value that is off the list, so the select shows what is saved', () => {
    expect(pingChoices(360)).toEqual([240, 360, 480, 720, 1440])
    expect(pingChoices(90)).toEqual([90, 240, 360, 480, 720, 1440])
    expect(pingChoices(null)).toEqual([240, 360, 480, 720, 1440])
  })

  it('words a period in hours, or minutes when it is not whole hours', () => {
    expect(formatPing(240)).toBe('4 hours')
    expect(formatPing(1440)).toBe('24 hours')
    expect(formatPing(60)).toBe('1 hour')
    expect(formatPing(90)).toBe('90 minutes')
  })
})
