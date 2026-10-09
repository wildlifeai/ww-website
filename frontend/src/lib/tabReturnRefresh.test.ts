import { describe, expect, it, vi } from 'vitest'
import { TAB_RETURN_REFRESH_MS, shouldRefreshOnReturn, subscribeToTabReturn } from './tabReturnRefresh'

describe('shouldRefreshOnReturn', () => {
  it('refreshes when nothing has loaded yet', () => {
    expect(shouldRefreshOnReturn(null, 1_000)).toBe(true)
  })

  it('waits 30 s after the last load started', () => {
    expect(TAB_RETURN_REFRESH_MS).toBe(30_000)
    expect(shouldRefreshOnReturn(100_000, 100_000)).toBe(false)
    expect(shouldRefreshOnReturn(100_000, 129_999)).toBe(false)
    expect(shouldRefreshOnReturn(100_000, 130_000)).toBe(true)
  })

  it('takes a custom interval', () => {
    expect(shouldRefreshOnReturn(0, 5, 10)).toBe(false)
    expect(shouldRefreshOnReturn(0, 10, 10)).toBe(true)
  })
})

function fakeDocument(visibilityState: DocumentVisibilityState) {
  return Object.assign(new EventTarget(), { visibilityState })
}

describe('subscribeToTabReturn', () => {
  it('calls back when the window regains focus', () => {
    const win = new EventTarget()
    const onReturn = vi.fn()
    subscribeToTabReturn(onReturn, win, fakeDocument('visible'))
    win.dispatchEvent(new Event('focus'))
    expect(onReturn).toHaveBeenCalledTimes(1)
  })

  it('calls back when the tab becomes visible, not when it is hidden', () => {
    const doc = fakeDocument('hidden')
    const onReturn = vi.fn()
    subscribeToTabReturn(onReturn, new EventTarget(), doc)
    doc.dispatchEvent(new Event('visibilitychange'))
    expect(onReturn).not.toHaveBeenCalled()
    doc.visibilityState = 'visible'
    doc.dispatchEvent(new Event('visibilitychange'))
    expect(onReturn).toHaveBeenCalledTimes(1)
  })

  it('stops listening once unsubscribed', () => {
    const win = new EventTarget()
    const doc = fakeDocument('visible')
    const onReturn = vi.fn()
    const unsubscribe = subscribeToTabReturn(onReturn, win, doc)
    unsubscribe()
    win.dispatchEvent(new Event('focus'))
    doc.dispatchEvent(new Event('visibilitychange'))
    expect(onReturn).not.toHaveBeenCalled()
  })

  it('refetches once when a tab switch fires both events, given the throttle', () => {
    const win = new EventTarget()
    const doc = fakeDocument('visible')
    let lastLoadAt: number | null = 0
    let loads = 0
    const now = 60_000
    subscribeToTabReturn(() => {
      if (shouldRefreshOnReturn(lastLoadAt, now)) { lastLoadAt = now; loads += 1 }
    }, win, doc)
    doc.dispatchEvent(new Event('visibilitychange'))
    win.dispatchEvent(new Event('focus'))
    expect(loads).toBe(1)
  })
})
