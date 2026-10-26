import { describe, expect, it } from 'vitest'
import { createLimiter } from './limiter'

const tick = () => new Promise(r => setTimeout(r, 1))

describe('createLimiter', () => {
  it('never runs more than the limit at once, across separate calls', async () => {
    const run = createLimiter(2)
    let inFlight = 0
    let peak = 0
    const task = async () => {
      inFlight++
      peak = Math.max(peak, inFlight)
      await tick()
      inFlight--
    }
    await Promise.all([run(task), run(task), ...Array.from({ length: 6 }, () => run(task))])
    expect(peak).toBe(2)
  })

  it('starts queued tasks in the order given', async () => {
    const run = createLimiter(1)
    const started: number[] = []
    await Promise.all([1, 2, 3].map(n => run(async () => { started.push(n); await tick() })))
    expect(started).toEqual([1, 2, 3])
  })

  it('gives each task its own result or error, and a failure frees its place', async () => {
    const run = createLimiter(1)
    const failed = run(async () => { throw new Error('no') })
    const ok = run(async () => 7)
    await expect(failed).rejects.toThrow('no')
    await expect(ok).resolves.toBe(7)
  })
})
