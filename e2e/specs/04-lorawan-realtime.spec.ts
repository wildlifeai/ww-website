import { test, expect } from '../helpers/test'
import { createClient } from '@supabase/supabase-js'
import { login, HAS_CREDS } from '../helpers/session'

/**
 * LoRaWAN live telemetry (#94): with the Realtime page (/field) open, a new message and its
 * decoded row must appear WITHOUT a reload, in the Cameras table and on top of the Messages tab.
 * The message carries a fault bit (self-test bit 9, the HM0360 camera), so the camera's row must
 * also show the health state ww-backend works out from it (deployment_latest_lorawan,
 * health_state, #427): Fault with the fault named, and its battery and temperature still showing.
 * A second message, the camera's answer to a settings downlink (kind op_set), must then appear on
 * top of the Messages tab in words (#516).
 * Picking the camera opens its check-in period panel for the run's user (tui@ww.org, a ww_admin,
 * by default). A settings request inserted as the service role, as lorawan-downlink records one,
 * must show there live while it waits, and again once it is answered (#521). No downlink reaches
 * TTN: the row is synthetic, marked by having no requester, which every request the function
 * records has; the spec deletes such rows of the seed deployment before and after, so a run that
 * died cannot leave the camera with a request in flight.
 * This is the end-to-end proof that (a) the realtime publication SQL is applied on the dev
 * database and (b) the page's subscription works. The page listens for the decoded
 * lorawan_parsed_messages row, which lorawan-ingest writes after the message, so the values are
 * there when it reads them; inserting the two rows in that order here is the ingest's order.
 *
 * Needs service-role access to insert synthetic telemetry:
 *   E2E_SUPABASE_URL + E2E_SUPABASE_SERVICE_ROLE_KEY
 * Seed deployment used: Zealandia Kiwi Watch (deterministic dev-seed UUID). It has no ping period
 * and no message. For the run it gets a 240-minute ping and its camera loses its DevEUI, so the
 * page lists it from the start as a LoRaWAN camera with no messages and must say why: "Not
 * registered for LoRaWAN" beside its state and under "need attention" (#525). The `finally` puts
 * both back as it found them.
 *
 * The rows carry the run's tag as their device_eui (E2E_RUN_TAG in CI, set by
 * .github/workflows/e2e.yml), so e2e/cleanup.mjs can find them when a run dies
 * before the `finally` below. The fault also sends a camera_fault notification to anyone with
 * that alert on in the seed project (ww-backend#359), and the ping can bring a camera_silent one;
 * the `finally` deletes those too.
 */

const SB_URL = process.env.E2E_SUPABASE_URL || ''
const SB_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''
const DEPLOYMENT_ID = process.env.E2E_LORAWAN_DEPLOYMENT_ID || 'e0000000-0000-0000-0000-000000000010'
// Distinctive values to assert on: both tabs show the volts (3123 mV is 3.1 V) and the SD card's
// free space, the Cameras table the temperature. Self-test bit 9 is a fault, which it names.
const BATTERY = 87
const BATTERY_MV = 3123
const SD_FREE = 37
const TEMPERATURE = -3
const HM0360_FAULT = 1 << 9
// The settings reply: op 16 set to a value no camera uses.
const OP_INDEX = 16
const OP_VALUE = 4321
// The settings request: op 32, the check-in period, set to 6 hours.
const CHECK_IN_OP = 32
const CHECK_IN_MINUTES = 360
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

    const startedAt = new Date().toISOString()
    // The seed rows as found, put back in the `finally`.
    const { data: seed, error: e0 } = await sb
      .from('deployments').select('device_id, lorawan_ping_minutes, devices(device_eui)').eq('id', DEPLOYMENT_ID).single()
    expect(e0, `read the seed deployment failed: ${e0?.message}`).toBeNull()
    const seedDevice = seed!.device_id as string
    const seedPing = seed!.lorawan_ping_minutes as number | null
    const seedEui = (seed!.devices as unknown as { device_eui: string | null } | null)?.device_eui ?? null
    try {
      await sb.from('deployments').update({ lorawan_ping_minutes: 240 }).eq('id', DEPLOYMENT_ID)
      await sb.from('devices').update({ device_eui: null }).eq('id', seedDevice)
      await login(page)
      await page.goto('/field')
      await page.waitForLoadState('networkidle')
      await expect.poll(() => subscribed, {
        message: 'the Realtime page never got "Subscribed to PostgreSQL" from Realtime for field-lorawan-live',
        timeout: 30_000,
      }).toBe(true)

      // A LoRaWAN camera with no DevEUI on record says why it has no messages.
      await expect(
        page.locator('#field-panel-cameras').getByRole('row').filter({ hasText: 'Not registered for LoRaWAN' }).first(),
        'the camera with no DevEUI did not say "Not registered for LoRaWAN" in the Cameras table',
      ).toBeVisible({ timeout: 30_000 })
      // The header "1 camera needs attention", whose parent holds the list.
      await expect(page.getByText(/cameras? needs? attention/).locator('..')).toContainText('Not registered for LoRaWAN')

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
          battery_level: BATTERY, battery_mv: BATTERY_MV, sd_free_pct: SD_FREE, temperature_c: TEMPERATURE,
          self_test_bits: HM0360_FAULT,
        })
      expect(e2, `insert lorawan_parsed_messages failed: ${e2?.message}`).toBeNull()

      // No reload: the camera's row must show the fault, named, with its volts and SD free.
      const camera = page.locator('#field-panel-cameras').getByRole('row').filter({ hasText: 'HM0360 camera failed' }).first()
      await expect(
        camera,
        'the fault did not appear live in the Cameras table. Is the realtime publication SQL ' +
        'applied to the dev DB (ALTER PUBLICATION supabase_realtime ADD TABLE lorawan_parsed_messages), ' +
        'and does deployment_latest_lorawan have health_state (ww-backend#381)?'
      ).toBeVisible({ timeout: 30_000 })
      await expect(camera).toContainText('Fault')
      await expect(camera).toContainText('3.1 V')
      await expect(camera).toContainText(`${SD_FREE}%`)
      await expect(camera).toContainText(`${TEMPERATURE} °C`)

      // Picking the camera opens its check-in period panel, offered to the run's admin user.
      await camera.click()
      const panel = page.locator('.card').filter({ hasText: 'Check-in period:' })
      await expect(panel, 'picking the camera did not open its check-in period panel').toBeVisible({ timeout: 15_000 })

      // The same message is on the Messages tab, as a check-in with its volts and SD free.
      await page.getByRole('tab', { name: 'Messages' }).click()
      const row = page.getByRole('row').filter({ hasText: '3.1 V' }).filter({ hasText: `${SD_FREE}%` }).first()
      await expect(row, 'the message did not appear live on the Messages tab').toBeVisible({ timeout: 30_000 })
      await expect(row).toContainText('Check-in')

      // The camera's answer to a settings downlink, also live, in words.
      const { data: reply, error: e3 } = await sb
        .from('lorawan_messages')
        .insert({
          device_eui: RUN_TAG,
          deployment_id: DEPLOYMENT_ID,
          raw_payload: { e2e_run: RUN_TAG },
          received_at: new Date().toISOString(),
        })
        .select('id')
        .single()
      expect(e3, `insert lorawan_messages failed: ${e3?.message}`).toBeNull()
      const { error: e4 } = await sb
        .from('lorawan_parsed_messages')
        .insert({ lorawan_message_id: reply!.id, kind: 'op_set', op_index: OP_INDEX, op_value: OP_VALUE })
      expect(e4, `insert lorawan_parsed_messages failed: ${e4?.message}`).toBeNull()
      await expect(
        page.getByRole('row').filter({ hasText: `Setting ${OP_INDEX} set to ${OP_VALUE}` }).first(),
        'the settings reply did not appear live on the Messages tab',
      ).toBeVisible({ timeout: 30_000 })

      // A settings request, as lorawan-downlink records one: waiting, then answered, both live.
      await sb.from('lorawan_downlinks').delete().eq('deployment_id', DEPLOYMENT_ID).is('requested_by', null)
      const { data: request, error: e6 } = await sb
        .from('lorawan_downlinks')
        .insert({
          deployment_id: DEPLOYMENT_ID, device_id: seedDevice, kind: 'op_set', op_index: CHECK_IN_OP,
          op_value: CHECK_IN_MINUTES, status: 'sent', sent_at: new Date().toISOString(),
        })
        .select('id')
        .single()
      expect(e6, `insert lorawan_downlinks failed: ${e6?.message}`).toBeNull()
      await expect(
        panel.getByRole('status'),
        'the settings request did not appear live in the panel. Is lorawan_downlinks in the ' +
        'supabase_realtime publication on dev (ww-backend#405)?',
      ).toContainText("Waiting for the camera's next check-in", { timeout: 30_000 })
      await expect(panel.getByRole('button', { name: 'Change' })).toBeDisabled()
      const { error: e7 } = await sb
        .from('lorawan_downlinks')
        .update({ status: 'answered', answered_at: new Date().toISOString() })
        .eq('id', request!.id)
      expect(e7, `update lorawan_downlinks failed: ${e7?.message}`).toBeNull()
      await expect(panel.getByRole('status'), 'the answer did not appear live in the panel')
        .toContainText('Check-in period set to 6 hours', { timeout: 30_000 })
    } finally {
      // Clean up the synthetic rows so repeated runs stay idempotent. The parsed
      // rows go with their messages (ON DELETE CASCADE); the fault's notifications do not.
      await sb.from('lorawan_messages').delete().eq('device_eui', RUN_TAG)
      await sb.from('lorawan_downlinks').delete().eq('deployment_id', DEPLOYMENT_ID).is('requested_by', null)
      // camera_silent too: with its ping, the camera is silent until the insert (ww-backend's alert).
      await sb.from('notifications').delete()
        .in('type', ['camera_fault', 'camera_silent']).eq('deployment_id', DEPLOYMENT_ID).gte('created_at', startedAt)
      await sb.from('deployments').update({ lorawan_ping_minutes: seedPing }).eq('id', DEPLOYMENT_ID)
      await sb.from('devices').update({ device_eui: seedEui }).eq('id', seedDevice)
    }
  })
})
