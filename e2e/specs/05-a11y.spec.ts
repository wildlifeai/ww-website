import AxeBuilder from '@axe-core/playwright'
import { test, expect } from '../helpers/test'

/**
 * Accessibility of the public pages, with axe-core: WCAG 2.0/2.1 A and AA
 * rules, failing on serious and critical violations only. Needs no account.
 * Runs as its own advisory job in CI until the pages are clean; the job's
 * name says so, so a red run is read as a list of things to fix, not noise.
 */

const PUBLIC_PAGES = ['/', '/login', '/signup', '/guides', '/faq', '/resources']

for (const path of PUBLIC_PAGES) {
  test(`no serious or critical accessibility violations on ${path}`, async ({ page }) => {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    const blocking = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    const summary = blocking
      .map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.length} nodes, e.g. ${v.nodes[0]?.target.join(' ')})`)
      .join('\n')
    expect(blocking, `\n${summary}\n`).toEqual([])
  })
}
