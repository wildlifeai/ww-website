import { test, expect } from '../helpers/test'

/**
 * Demo flow: "Try the demo" signs the visitor into the shared read-only
 * account (POST /api/auth/demo-session) and DemoGuard blocks mutations.
 */

test('demo button opens a signed-in read-only session', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /try the demo/i }).click()
  // The button reports failure states inline (e.g. DEMO_DISABLED: "The demo
  // account is not configured on this server.") — fail fast and loudly on those.
  await expect(page.getByText(/demo.*(unavailable|disabled|not configured)/i)).not.toBeVisible({ timeout: 15_000 })
  // Success = we leave the marketing page for an authed view.
  await page.waitForURL(url => url.pathname !== '/', { timeout: 60_000 })
  const landed = new URL(page.url()).pathname
  expect(landed, 'demo login did not navigate into the app').not.toMatch(/^\/(login)?$/)
})

test('demo session is read-only where it matters', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /try the demo/i }).click()
  await page.waitForURL(url => url.pathname !== '/', { timeout: 60_000 })

  // Attempting an upload from the demo account must be gated by DemoGuard:
  // either the control is absent/disabled, or activating it shows the guard
  // message instead of the upload modal's folder picker.
  await page.goto('/toolkit')
  const uploadEntry = page.getByRole('button', { name: /upload/i }).first()
  if (await uploadEntry.isVisible().catch(() => false)) {
    const disabled = await uploadEntry.isDisabled().catch(() => false)
    if (!disabled) {
      await uploadEntry.click()
      await expect(
        page.getByText(/demo|read.?only|sign up|create (an )?account/i).first()
      ).toBeVisible({ timeout: 10_000 })
    }
  }
})
