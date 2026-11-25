import { test, expect } from '../helpers/test'
import { createClient } from '@supabase/supabase-js'
import { login, HAS_CREDS } from '../helpers/session'

/**
 * LoRaWAN live telemetry (#94): with the Realtime page (/field) open, a new message and its
 * decoded row must appear WITHOUT a reload, in the Cameras table and on top of the Messages tab.
 * This is the end-to-end proof that (a) the realtime publication SQL is applied on the dev
 * database and (b) the page's subscription works. The page listens for the decoded
 * lorawan_parsed_messages row, which lorawan-ingest writes after the message, so the values are
 * there when it reads them; inserting the two rows in that order here is the ingest's order.
 *
 * Needs service-role access to insert synthetic telemetry:
 *   E2E_SUPABASE_URL + E2E_SUPABASE_SERVICE_ROLE_KEY
 * Seed deployment used: Zealandia Kiwi Watch (deterministic dev-seed UUID).
 *
 * The rows carry the run's tag as their device_eui (E2E_RUN_TAG in CI, set by
 * .github/workflows/e2e.yml), so e2e/cleanup.mjs can find them when a run dies
 * before the `finally` below.
 */

const SB_URL = process.env.E2E_SUPABASE_URL || ''
const SB_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''
const DEPLOYMENT_ID = process.env.E2E_LORAWAN_DEPLOYMENT_ID || 'e0000000-0000-0000-0000-000000000010'
// Distinctive values to assert on: the Cameras table shows the percentage, the Messages tab
// the volts (3123 mV is 3.1 V) and the SD card's free space.
const BATTERY = 87
const BATTERY_MV = 3123
const SD_FREE = 37
const RUN_TAG = process.env.E2E_RUN_TAG || `e2e-local-${Date.now()}`

test.describe('LoRaWAN realtime telemetry', () => {
  test.skip(!HAS_CREDS, 'E2E_EMAIL/E2E_PASSWORD not set')
  test.skip(!SB_URL || !SB_KEY, 'E2E_SUPABASE_URL / E2E_SUPABASE_SERVICE_ROLE_KEY not set')

  test('new telemetry appears on the Realtime page without reload', async ({ page }) => {
    const sb = createClient(SB_URL, SB_KEY, { auth: { persistSession: false } })

    // The page subscribes once its deployments have loaded, over a websocket that
    // networkidle does not wait for. An insert before Realtime confirms the
    // subscription is never delivered, so wait for that confirmation first. The
    // channel's name starts field-lorawan-live (hooks/useFieldLive.ts).
    let subscribed = false
    page.on('websocket', (ws) => ws.on('framereceived', ({ payload }) => {
      const text = typeof payload === 'string' ? payload : payload.toString()
      if (text.includes('realtime:field-lorawan-live') && text.includes('Subscribed to PostgreSQL')) subscribed = true
    }))

    try {
      await login(page)
      await page.goto('/field')
      await page.waitForLoadState('networkidle')
      await expect.poll(() => subscribed, {
        message: 'the Realtime page never got "Subscribed to PostgreSQL" from Realtime for field-lorawan-live',
        timeout: 30_000,
      }).toBe(true)

      // Insert a synthetic uplink AFTER the page is subscribed, then its decoded values.
      const { data: msg, error: e1 } = await sb
        .from('lorawan_messages')
        .insert({
          device_eui: RUN_TAG,
          deployment_id: DEPLOYMENT_ID,
          raw_payload: { e2e_run: RUN_TAG },
          received_at: new Date().toISOString(),
        })
        .select('id')
        .single()
      expect(e1, `insert lorawan_messages failed: ${e1?.message}`).toBeNull()
      const { error: e2 } = await sb
        .from('lorawan_parsed_messages')
        .insert({
          lorawan_message_id: msg!.id, kind: 'report',
          battery_level: BATTERY, battery_mv: BATTERY_MV, sd_free_pct: SD_FREE,
        })
      expect(e2, `insert lorawan_parsed_messages failed: ${e2?.message}`).toBeNull()

      // No reload: the distinctive battery value must appear via realtime.
      await expect(
        page.getByText(new RegExp(`${BATTERY}\\s*%`)).first(),
        'telemetry did not appear live in the Cameras table. Is the realtime publication SQL ' +
        'applied to the dev DB (ALTER PUBLICATION supabase_realtime ADD TABLE lorawan_parsed_messages)?'
      ).toBeVisible({ timeout: 30_000 })

      // The same message is on the Messages tab, as a check-in with its volts and SD free.
      await page.getByRole('tab', { name: 'Messages' }).click()
      const row = page.getByRole('row').filter({ hasText: '3.1 V' }).filter({ hasText: `${SD_FREE}%` }).first()
      await expect(row, 'the message did not appear live on the Messages tab').toBeVisible({ timeout: 30_000 })
      await expect(row).toContainText('Check-in')
    } finally {
      // Clean up the synthetic rows so repeated runs stay idempotent. The parsed
      // rows go with their message (ON DELETE CASCADE).
      await sb.from('lorawan_messages').delete().eq('device_eui', RUN_TAG)
    }
  })
})
