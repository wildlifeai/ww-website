import { describe, expect, it } from 'vitest'
import { readAllPages } from './readAllPages'

describe('readAllPages', () => {
  it('reads one short page and stops', async () => {
    const ranges: [number, number][] = []
    const rows = await readAllPages((from, to) => {
      ranges.push([from, to])
      return Promise.resolve({ data: [1, 2], error: null })
    }, 3)
    expect(rows).toEqual([1, 2])
    expect(ranges).toEqual([[0, 2]])
  })

  it('reads past a full page until a short one', async () => {
    const pages = [[1, 2, 3], [4, 5, 6], [7]]
    const ranges: [number, number][] = []
    const rows = await readAllPages((from, to) => {
      ranges.push([from, to])
      return Promise.resolve({ data: pages[ranges.length - 1], error: null })
    }, 3)
    expect(rows).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(ranges).toEqual([[0, 2], [3, 5], [6, 8]])
  })

  it('reads one more empty page when the total is a multiple of the page size', async () => {
    const pages = [[1, 2], []]
    let n = 0
    const rows = await readAllPages(() => Promise.resolve({ data: pages[n++], error: null }), 2)
    expect(rows).toEqual([1, 2])
    expect(n).toBe(2)
  })

  it('treats a null page as empty', async () => {
    expect(await readAllPages(() => Promise.resolve({ data: null, error: null }))).toEqual([])
  })

  it('throws the error instead of returning a short list', async () => {
    const pages = [{ data: [1, 2], error: null }, { data: null, error: { message: 'denied' } }]
    let n = 0
    await expect(readAllPages(() => Promise.resolve(pages[n++]), 2)).rejects.toThrow('denied')
  })
})
