/**
 * Which header tab a route belongs to, so a deep page (a cluster review, a report, the SD card
 * builder) lights its parent tab instead of none (UI audit 01 A3). Upload, Settings,
 * Notifications and Processing history belong to the Upload button and the account menu, not a tab.
 */

export type NavTabId = 'toolkit' | 'field' | 'annotations' | 'insights'

const PARENT_TAB: [prefix: string, tab: NavTabId][] = [
  ['/toolkit', 'toolkit'],
  ['/manifest', 'toolkit'],
  ['/live-view', 'toolkit'],
  ['/upload-model', 'toolkit'],
  ['/field', 'field'],
  ['/annotations', 'annotations'],
  ['/explore', 'annotations'],
  ['/clusters', 'annotations'],
  ['/review', 'annotations'],
  ['/umap', 'annotations'],
  ['/insights', 'insights'],
  ['/reporting', 'insights'],
  ['/analysis', 'insights'],
  ['/intelligence', 'insights'],
]

export function activeNavTab(pathname: string): NavTabId | null {
  for (const [prefix, tab] of PARENT_TAB) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return tab
  }
  return null
}
