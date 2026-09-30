import { describe, expect, it } from 'vitest'
import { burstCostNote, formatInterval, photoCountOptions, photoIntervalOptions } from './burstCapture'

describe('burstCapture', () => {
  it('offers 1 to 10 photos per trigger', () => {
    expect(photoCountOptions()).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
  })

  it('offers 200 to 2000 ms in 100 ms steps', () => {
    const options = photoIntervalOptions(1000)
    expect(options[0]).toBe(200)
    expect(options[options.length - 1]).toBe(2000)
    expect(options).toHaveLength(19)
  })

  it('keeps an off-grid current value selectable, in order', () => {
    const options = photoIntervalOptions(1250)
    expect(options).toHaveLength(20)
    expect(options.indexOf(1250)).toBe(options.indexOf(1200) + 1)
  })

  it('formats the interval in seconds', () => {
    expect(formatInterval(1000)).toBe('1 s')
    expect(formatInterval(200)).toBe('0.2 s')
    expect(formatInterval(1250)).toBe('1.25 s')
  })

  it('states the cost of a burst, and nothing for a single photo', () => {
    expect(burstCostNote(1)).toBeNull()
    expect(burstCostNote(3)).toBe('Each trigger uses about 3× the card space and battery of a single photo.')
  })
})
