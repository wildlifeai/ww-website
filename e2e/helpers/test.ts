import { test as base, expect } from '@playwright/test'

/**
 * The suite's `test`: Playwright's, plus a record of the page's console errors
 * and uncaught exceptions, printed with the page's URL and visible text when a
 * test fails. In CI that block in the job log is how a failure is read, since
 * traces are off there (see playwright.config.ts).
 */
export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on('console', (m) => {
        if (m.type() === 'error') errors.push(m.text())
      })
      page.on('pageerror', (e) => errors.push(`uncaught: ${e.message}`))
      await use(errors)
    },
    { auto: true },
  ],
})

test.afterEach(async ({ page, consoleErrors }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) return
  const url = page.url()
  const text = await page.locator('body').innerText().catch(() => '(page gone)')
  const errors = consoleErrors.slice(0, 20).map((e) => `    ${e.slice(0, 300)}`).join('\n')
  console.log(
    [
      '',
      `[e2e] FAILED: ${testInfo.titlePath.join(' > ')}`,
      `  url: ${url}`,
      `  console errors (${consoleErrors.length}):`,
      errors || '    (none)',
      '  page text (first 2000 chars):',
      text.slice(0, 2000).replace(/^/gm, '    '),
      '',
    ].join('\n'),
  )
})

export { expect }
