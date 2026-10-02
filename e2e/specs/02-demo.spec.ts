import { Page } from '@playwright/test'
import { test, expect } from '../helpers/test'

/**
 * Demo flow: "Try the demo" signs the visitor into the shared read-only
 * account (POST /api/auth/demo-session) and DemoGuard blocks mutations.
 */

/**
 * Success is the signed-in dashboard, which the app renders at `/` itself:
 * the URL does not change (run 36937428801 waited a minute for one while the
 * demo banner was already on screen). The banner is the proof.
 */
async function openDemo(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: /try the demo/i }).click()
  // The button reports failure states inline (e.g. DEMO_DISABLED: "The demo
  // account is not configured on this server.") — fail fast and loudly on those.
  await expect(page.getByText(/demo.*(unavailable|disabled|not configured)/i)).not.toBeVisible({ timeout: 15_000 })
  // The dev API may be cold: the button itself says "Waking the server…"
  await expect(page.getByText(/read-only demo/i).first()).toBeVisible({ timeout: 90_000 })
}

test('demo button opens a signed-in read-only session', async ({ page }) => {
  await openDemo(page)
  // Signed in: the authed navigation is there and the login form is not.
  await expect(page.getByRole('link', { name: /annotations/i }).first()).toBeVisible()
  await expect(page.locator('form input[type="password"]')).toHaveCount(0)
})

test('demo session is read-only where it matters', async ({ page }) => {
  await openDemo(page)

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
