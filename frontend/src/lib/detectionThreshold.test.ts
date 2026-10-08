import { describe, expect, it } from 'vitest'
import { clampThreshold } from './detectionThreshold'

describe('detectionThreshold', () => {
  it('keeps a value inside 50 to 99', () => {
    expect(clampThreshold('50', 57)).toBe(50)
    expect(clampThreshold('80', 57)).toBe(80)
    expect(clampThreshold('99', 57)).toBe(99)
  })

  it('clamps to the column range', () => {
    expect(clampThreshold('10', 57)).toBe(50)
    expect(clampThreshold('120', 57)).toBe(99)
  })

  it('rounds to a whole percent', () => {
    expect(clampThreshold('72.6', 57)).toBe(73)
  })

  it('keeps the current value for an empty or non-numeric input', () => {
    expect(clampThreshold('', 65)).toBe(65)
    expect(clampThreshold('  ', 65)).toBe(65)
    expect(clampThreshold('abc', 65)).toBe(65)
  })
})
