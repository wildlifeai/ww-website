import { describe, expect, it } from 'vitest'
import { settleWithLimit } from './settleWithLimit'

describe('settleWithLimit', () => {
  it('keeps input order and reports each failure', async () => {
    const res = await settleWithLimit([1, 2, 3], 2, async n => {
      if (n === 2) throw new Error('two')
      return n * 10
    })
    expect(res[0]).toEqual({ status: 'fulfilled', value: 10 })
    expect(res[1].status).toBe('rejected')
    expect((res[1] as PromiseRejectedResult).reason).toEqual(new Error('two'))
    expect(res[2]).toEqual({ status: 'fulfilled', value: 30 })
  })

  it('never has more than the limit in flight', async () => {
    let inFlight = 0
    let peak = 0
    await settleWithLimit(Array.from({ length: 20 }, (_, i) => i), 4, async () => {
      inFlight++
      peak = Math.max(peak, inFlight)
      await new Promise(r => setTimeout(r, 1))
      inFlight--
    })
    expect(peak).toBe(4)
  })

  it('returns an empty list for no items', async () => {
    await expect(settleWithLimit([], 4, async () => 1)).resolves.toEqual([])
  })
})
