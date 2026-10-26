import { describe, expect, it } from 'vitest'
import { activeNavTab } from './navTabs'

describe('activeNavTab', () => {
  it('lights the tab of its own route', () => {
    expect(activeNavTab('/toolkit')).toBe('toolkit')
    expect(activeNavTab('/field')).toBe('field')
    expect(activeNavTab('/annotations')).toBe('annotations')
    expect(activeNavTab('/insights')).toBe('insights')
  })

  it('maps deep routes to their parent tab', () => {
    expect(activeNavTab('/manifest')).toBe('toolkit')
    expect(activeNavTab('/live-view')).toBe('toolkit')
    expect(activeNavTab('/upload-model')).toBe('toolkit')
    expect(activeNavTab('/clusters/d1')).toBe('annotations')
    expect(activeNavTab('/review/d1')).toBe('annotations')
    expect(activeNavTab('/umap/d1')).toBe('annotations')
    expect(activeNavTab('/reporting/d1')).toBe('insights')
    expect(activeNavTab('/intelligence/p1')).toBe('insights')
  })

  it('leaves the menu and upload pages without a tab', () => {
    for (const path of ['/', '/settings', '/notifications', '/processing', '/upload-data', '/upload/logs']) {
      expect(activeNavTab(path)).toBeNull()
    }
  })

  it('matches a whole path segment, not a prefix of a word', () => {
    expect(activeNavTab('/reviewers')).toBeNull()
    expect(activeNavTab('/fieldwork')).toBeNull()
  })
})
