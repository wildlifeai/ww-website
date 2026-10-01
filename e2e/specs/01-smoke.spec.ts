import { test, expect } from '../helpers/test'
import { login, expectHealthyRender, warmApi, API_URL, HAS_CREDS } from '../helpers/session'

/**
 * Smoke: the dev website is up, the API answers, public pages render,
 * and the seeded user can sign in and reach every authed page.
 */

test('backend API is up and answering', async ({ request }) => {
  // FastAPI serves /docs unauthenticated; any 200 proves the container is
  // alive. The dev API scales to zero, so the first answer can take a minute:
  // this test absorbs the cold start so the page tests after it do not.
  test.setTimeout(240_000)
  const ms = await warmApi(request)
  console.log(`[e2e] API answered after ${Math.round(ms / 1000)}s`)
})

test('public pages render without errors', async ({ page }) => {
  for (const path of ['/', '/guides', '/faq', '/resources']) {
    const errors = await expectHealthyRender(page, path)
    expect(errors, `uncaught errors on ${path}: ${errors.join('; ')}`).toHaveLength(0)
  }
  // The marketing hero must offer both entry points.
  await page.goto('/')
  await expect(page.getByRole('link', { name: /log ?in|sign ?in/i }).or(page.getByRole('button', { name: /log ?in|sign ?in/i })).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /try the demo/i })).toBeVisible()
})

test('seeded user can sign in and reach all authed pages', async ({ page }) => {
  test.skip(!HAS_CREDS, 'E2E_EMAIL/E2E_PASSWORD not set')
  await login(page)
  for (const path of ['/toolkit', '/field', '/annotations', '/insights', '/settings']) {
    // Navigate in-SPA (nav links) — full reloads drop the session in this app.
    const link = page.locator(`a[href="${path}"]`).first()
    if (await link.isVisible().catch(() => false)) await link.click()
    else await page.goto(path)
    await page.waitForLoadState('networkidle')
    await expect(page.locator('body')).not.toContainText(/something went wrong|application error/i)
    // RequireAuth bounces to /login when the session is broken — catch it.
    expect(new URL(page.url()).pathname, `bounced off ${path}`).not.toMatch(/^\/login/)
  }
})
