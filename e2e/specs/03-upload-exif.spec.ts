import { test, expect } from '../helpers/test'
import { login, HAS_CREDS } from '../helpers/session'
import * as path from 'path'
import * as fs from 'fs'

/**
 * Upload pipeline: a real SD-card folder upload through the UI, then verify
 * EXIF-driven binding.
 *
 * The fixture set (test-fixtures/camera-trap/, built by prepare.py) stamps
 * each JPG's EXIF UserComment with a deployment UUID and GPS coordinates:
 *  - UUIDs e0000000-…-0010/0011/… exist in the dev seeds → exact-match binding
 *  - at least one stamped UUID has NO deployments row → exercises the #92
 *    auto-create path (deployment created reusing the stamped UUID)
 * so after a successful upload NOTHING may remain unassigned.
 */

const SDCARD = path.resolve(__dirname, '../../test-fixtures/camera-trap/sdcard/dev-sdcard')
const DEPLOYMENTS = path.resolve(__dirname, '../../test-fixtures/camera-trap/deployments.json')

test.describe('SD-card upload with EXIF deployment binding', () => {
  test.skip(!HAS_CREDS, 'E2E_EMAIL/E2E_PASSWORD not set')
  test.skip(!fs.existsSync(SDCARD), `fixture folder missing: ${SDCARD} (run test-fixtures/camera-trap/prepare.py)`)

  test('upload fixture SD card; all media bound to deployments', async ({ page }) => {
    test.setTimeout(600_000) // real upload + processing

    const fixtures = JSON.parse(fs.readFileSync(DEPLOYMENTS, 'utf8')).fixtures as
      { name: string; deployment_id: string }[]

    await login(page)
    await page.goto('/toolkit')

    // Open the upload modal and feed it the whole SD-card directory
    // (the input is webkitdirectory — Playwright accepts a folder path).
    await page.getByRole('button', { name: /upload/i }).first().click()
    const dirInput = page.locator('input[webkitdirectory], input[type="file"]').first()
    await dirInput.setInputFiles(SDCARD)

    // Step 2 of the modal: summary + resolved deployments, then start.
    await expect(page.getByText(/image/i).first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: /^upload/i }).last().click()

    // The modal closes and the upload runs in the background; wait for the
    // progress UI to finish rather than a fixed sleep.
    const progress = page.getByText(/uploading|processing|\d+\s*\/\s*\d+/i).first()
    if (await progress.isVisible().catch(() => false)) {
      await expect(progress).toBeHidden({ timeout: 480_000 })
    }

    // Verify through the app's own (RLS-scoped) session that the batch landed
    // bound: recent media of this user must have a deployment_id, and the
    // seeded deployment names from the fixture set must now own new media.
    await page.goto('/annotations')
    await page.waitForLoadState('networkidle')
    for (const f of fixtures.slice(0, 2)) {
      await expect(
        page.getByText(f.name).first(),
        `deployment "${f.name}" (exact EXIF match) missing from annotations view`
      ).toBeVisible({ timeout: 60_000 })
    }
    // #92 regression guard: nothing may sit in an "unassigned" bucket.
    await expect(page.getByText(/unassigned|no deployment/i)).not.toBeVisible()
  })
})
