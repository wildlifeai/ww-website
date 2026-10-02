import { test, expect } from '../helpers/test'
import { createClient } from '@supabase/supabase-js'
import { login, HAS_CREDS } from '../helpers/session'

/**
 * LoRaWAN live telemetry (#94): with the Field page open, a new
 * lorawan_messages row must appear in the UI WITHOUT a reload — this is the
 * end-to-end proof that (a) the realtime publication SQL is applied on the
 * dev database and (b) the FieldPage subscription works.
 *
 * Needs service-role access to insert synthetic telemetry:
 *   E2E_SUPABASE_URL + E2E_SUPABASE_SERVICE_ROLE_KEY
 * Seed deployment used: Zealandia Kiwi Watch (deterministic dev-seed UUID).
 */

const SB_URL = process.env.E2E_SUPABASE_URL || ''
const SB_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''
const DEPLOYMENT_ID = process.env.E2E_LORAWAN_DEPLOYMENT_ID || 'e0000000-0000-0000-0000-000000000010'
const BATTERY = 87 // distinctive value to assert on

test.describe('LoRaWAN realtime telemetry', () => {
  test.skip(!HAS_CREDS, 'E2E_EMAIL/E2E_PASSWORD not set')
  test.skip(!SB_URL || !SB_KEY, 'E2E_SUPABASE_URL / E2E_SUPABASE_SERVICE_ROLE_KEY not set')

  test('new telemetry appears on the Field page without reload', async ({ page }) => {
    const sb = createClient(SB_URL, SB_KEY, { auth: { persistSession: false } })
    let messageId: string | null = null

    try {
      await login(page)
      await page.goto('/field')
      await page.waitForLoadState('networkidle')

      // Insert a synthetic uplink AFTER the page is subscribed.
      const { data: msg, error: e1 } = await sb
        .from('lorawan_messages')
        .insert({ deployment_id: DEPLOYMENT_ID, received_at: new Date().toISOString() })
        .select('id')
        .single()
      expect(e1, `insert lorawan_messages failed: ${e1?.message}`).toBeNull()
      messageId = msg!.id
      const { error: e2 } = await sb
        .from('lorawan_parsed_messages')
        .insert({ message_id: messageId, battery_level: BATTERY, sd_card_used_capacity: 41 })
      expect(e2, `insert lorawan_parsed_messages failed: ${e2?.message}`).toBeNull()

      // No reload: the distinctive battery value must appear via realtime.
      await expect(
        page.getByText(new RegExp(`${BATTERY}\\s*%`)).first(),
        'telemetry did not appear live — is the realtime publication SQL applied ' +
        'to the dev DB (ALTER PUBLICATION supabase_realtime ADD TABLE lorawan_messages…)?'
      ).toBeVisible({ timeout: 30_000 })
    } finally {
      // Clean up the synthetic rows so repeated runs stay idempotent.
      if (messageId) {
        await sb.from('lorawan_parsed_messages').delete().eq('message_id', messageId)
        await sb.from('lorawan_messages').delete().eq('id', messageId)
      }
    }
  })
})
