import { describe, expect, it } from 'vitest'
import { PAGE_SIZE, pagerText, pageWindow } from './gridPage'

describe('pageWindow', () => {
  it('describes a middle page', () => {
    expect(pageWindow(2, PAGE_SIZE, 1000)).toEqual({ from: 201, to: 300, total: 1000, hasPrev: true, hasNext: true })
  })

  it('ends on the last page', () => {
    expect(pageWindow(9, PAGE_SIZE, 1000)).toMatchObject({ from: 901, to: 1000, hasNext: false })
    expect(pageWindow(0, 37, 37)).toEqual({ from: 1, to: 37, total: 37, hasPrev: false, hasNext: false })
  })

  it('never reads less than the page shows when the count lags an upload', () => {
    expect(pageWindow(0, 60, 0)).toMatchObject({ from: 1, to: 60, total: 60, hasNext: false })
  })

  it('offers Next after a full page until the first count arrives', () => {
    expect(pageWindow(0, PAGE_SIZE, null).hasNext).toBe(true)
    expect(pageWindow(0, PAGE_SIZE, 100).hasNext).toBe(false)
  })

  it('handles an empty page', () => {
    expect(pageWindow(0, 0, 0)).toEqual({ from: 0, to: 0, total: 0, hasPrev: false, hasNext: false })
  })
})

describe('pagerText', () => {
  it('gives the range across pages and the count on one page', () => {
    expect(pagerText(pageWindow(1, PAGE_SIZE, 350))).toBe('101–200 of 350')
    expect(pagerText(pageWindow(0, 37, 37))).toBe('37 photos')
    expect(pagerText(pageWindow(0, 1, 1))).toBe('1 photo')
  })

  it('says nothing before the first count', () => {
    expect(pagerText(pageWindow(0, 20, null))).toBeNull()
  })
})
