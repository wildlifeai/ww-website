import { test, expect } from '../helpers/test'
import { login, HAS_CREDS } from '../helpers/session'
import * as path from 'path'
import * as fs from 'fs'

/**
 * Upload pipeline: a real SD-card folder upload through /upload-data, then the
 * EXIF-driven binding.
 *
 * The fixture card (test-fixtures/camera-trap/sdcard/dev-sdcard) holds real
 * WW500 frames: each MEDIA/<folder> carries its deployment's UUID in EXIF tag
 * 0xF200, and the folder is the UUID's first eight characters.
 *  - Most of those deployments are in ww-backend's dev seed, so their photos
 *    match by exact id.
 *  - At least one (EAB2AA23) is not: the page sends it to triage, and the spec
 *    creates the deployment under the stamped id, the #92 path. Later runs then
 *    find it, since cleanup.mjs removes the media but not the deployment.
 *  - MEDIA/00000000 is test photos with no deployment set, which the page
 *    skips (#287).
 * So after the upload nothing may be left unassigned, and every deployment it
 * touched is one of the card's folders.
 *
 * AI analysis is switched off: this spec checks the upload and the binding,
 * and the AI pipeline on dev would only add time and its own failures.
 */

const SDCARD = path.resolve(__dirname, '../../test-fixtures/camera-trap/sdcard/dev-sdcard')
const FOLDERS = fs.existsSync(SDCARD)
  ? fs.readdirSync(path.join(SDCARD, 'MEDIA')).filter(f => f !== '00000000').map(f => f.toUpperCase())
  : []

test.describe('SD-card upload with EXIF deployment binding', () => {
  test.skip(!HAS_CREDS, 'E2E_EMAIL/E2E_PASSWORD not set')
  test.skip(!fs.existsSync(SDCARD), `fixture folder missing: ${SDCARD}`)

  test('upload fixture SD card; all media bound to deployments', async ({ page }) => {
    test.setTimeout(600_000) // real upload + Drive sync

    await login(page)
    await page.goto('/upload-data')

    // The drop zone's folder input is hidden (webkitdirectory); Playwright
    // accepts a folder path on it directly, no click needed.
    await page.locator('input[webkitdirectory]').setInputFiles(SDCARD)

    // The page reads every frame's EXIF before it enables Upload.
    const upload = page.getByRole('button', { name: /^⬆ Upload \d+ images?$/ })
    await expect(upload).toBeEnabled({ timeout: 120_000 })
    await page.getByRole('checkbox', { name: /Run AI analysis after upload/ }).uncheck()
    await upload.click()

    // Photos whose stamped deployment is not in the database go to triage.
    // Create each one under its stamped id, then continue.
    const decide = page.getByRole('button', { name: /still to decide|^Continue upload$/ })
    await expect(decide.or(page.getByText(/^Uploading \d+ images?…$/))).toBeVisible({ timeout: 60_000 })
    if (await decide.isVisible()) {
      const create = page.getByRole('button', { name: 'Create deployment from these photos' })
      for (let n = 1; (await create.count()) > 0; n++) {
        await create.first().click()
        const name = page.locator('.triage-create input').first()
        if (!(await name.inputValue()).trim()) await name.fill(`E2E card session ${n}`)
        await page.getByRole('button', { name: 'Create & assign' }).click()
        await expect(page.locator('.triage-create')).toHaveCount(0, { timeout: 60_000 })
      }
      await page.getByRole('button', { name: /^Continue upload$/ }).click()
    }

    // The page moves to Annotations filtered to every deployment the upload
    // touched: each must be one of the card's folders.
    await page.waitForURL(/\/annotations\?deployment=/, { timeout: 60_000 })
    const touched = (new URL(page.url()).searchParams.get('deployment') ?? '').split(',').filter(Boolean)
    expect(touched.length, 'the upload touched no deployment').toBeGreaterThan(0)
    for (const id of touched) {
      expect(FOLDERS, `deployment ${id} is not one of the card's folders`).toContain(id.slice(0, 8).toUpperCase())
    }

    // The progress dock follows the upload and the Drive sync to the end.
    const outcome = page.getByText(/^(Upload & analysis complete|Upload failed)$/).first()
    await expect(outcome).toBeVisible({ timeout: 480_000 })
    await expect(outcome).toHaveText('Upload & analysis complete')

    // #92 regression guard: nothing may sit in an "unassigned" bucket.
    await expect(page.getByText(/unassigned|no deployment/i)).not.toBeVisible()
  })
})
