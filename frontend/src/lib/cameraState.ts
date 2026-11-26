/**
 * A camera's state on the Realtime page (/field), one function for the table, the attention list
 * and the KPI cards (ww-website#427). The rules:
 *
 * - Finished once its deployment has ended (the page shows those that ended today).
 * - LoRaWAN off when the deployment's `lorawan_ping_minutes` is 0: never Silent.
 * - Silent after 3 ping periods with no message of any kind, 12 h at the 240-minute ping a
 *   deployed camera uses. With no message yet it counts from the deployment's start, as
 *   ww-backend's `camera_silent` alert does. A deployment with no period recorded (null, every
 *   one made before ww-mobile-app#532) keeps the old rule: Silent after 72 h, never before the
 *   first message. Silent overrides the health state.
 * - Awaiting before the first message.
 * - Otherwise the health state ww-backend works out once for the website, the app and the alerts
 *   (`deployment_latest_lorawan.health_state`, ww-backend#381): Fault, Warning or OK. A camera
 *   that sends no health report (today's field firmware) is Checking in, green (Victor, 26 Nov
 *   2026). With no period recorded, a camera whose last message is over 24 h old is Stale unless
 *   it reports a Fault or Warning.
 */

export type HealthState = 'fault' | 'warning' | 'ok'

export type CameraState =
  | 'finished' | 'off' | 'silent' | 'awaiting' | 'fault' | 'warning' | 'stale' | 'ok' | 'checking_in'

/** With no period recorded: Stale after this many hours, Silent after `STALE_HOURS`. */
export const FRESH_HOURS = 24
export const STALE_HOURS = 72
/** Ping periods with no message before a camera is Silent. */
export const SILENT_PERIODS = 3

export interface CameraFacts {
  /** The newest message of any kind. */
  lastSeen: string | null
  deploymentStart: string | null
  deploymentEnd: string | null
  /** `deployments.lorawan_ping_minutes`: 0 is LoRaWAN off, null not recorded. */
  pingMinutes: number | null
  health: HealthState | null
}

/** The view's `health_state` as a state, null for anything else. */
export function toHealthState(value: string | null | undefined): HealthState | null {
  return value === 'fault' || value === 'warning' || value === 'ok' ? value : null
}

/** Hours with no message before the camera is Silent; null when it never is (LoRaWAN off). */
export function silentAfterHours(pingMinutes: number | null): number | null {
  if (pingMinutes === 0) return null
  if (pingMinutes == null) return STALE_HOURS
  return (SILENT_PERIODS * pingMinutes) / 60
}

export function cameraState(camera: CameraFacts, now = Date.now()): CameraState {
  const { lastSeen, deploymentStart, deploymentEnd, pingMinutes, health } = camera
  if (deploymentEnd && Date.parse(deploymentEnd) <= now) return 'finished'
  const silentAfter = silentAfterHours(pingMinutes)
  if (silentAfter == null) return 'off'
  const hoursSince = (iso: string) => (now - Date.parse(iso)) / 3_600_000
  const quietSince = lastSeen ?? (pingMinutes != null ? deploymentStart : null)
  if (quietSince && hoursSince(quietSince) > silentAfter) return 'silent'
  if (!lastSeen) return 'awaiting'
  if (health === 'fault' || health === 'warning') return health
  if (pingMinutes == null && hoursSince(lastSeen) > FRESH_HOURS) return 'stale'
  return health ?? 'checking_in'
}

/** Silent, Fault and Warning cameras are on the "needs attention" list. */
export function needsAttention(state: CameraState): boolean {
  return state === 'silent' || state === 'fault' || state === 'warning'
}

export const CAMERA_STATE: Record<CameraState, { color: string; label: string }> = {
  finished:    { color: '#3b82f6', label: 'Finished' },
  off:         { color: '#9ca3af', label: 'LoRaWAN off' },
  silent:      { color: '#ef4444', label: 'Silent' },
  awaiting:    { color: '#9ca3af', label: 'Awaiting' },
  fault:       { color: '#ef4444', label: 'Fault' },
  warning:     { color: '#f59e0b', label: 'Warning' },
  stale:       { color: '#f59e0b', label: 'Stale' },
  ok:          { color: '#22c55e', label: 'OK' },
  checking_in: { color: '#22c55e', label: 'Checking in' },
}

/** "12 h", "3 days": how long a camera goes unheard before it is Silent. */
export function silenceText(pingMinutes: number | null): string {
  const hours = silentAfterHours(pingMinutes) ?? 0
  const span = hours >= 48 && hours % 24 === 0 ? `${hours / 24} days`
    : hours >= 1 ? `${Math.round(hours * 10) / 10} h`
      : `${Math.round(hours * 60)} min`
  return `No message for over ${span}`
}

// Self-test bits by number, after the firmware's selfTest.h and the app's decodeSelfTest
// (ww-mobile-app src/utils/deviceSelfTest.ts); bits 6, 7 and 15 have no name.
const SELF_TEST_BITS: Record<number, string> = {
  0: 'Battery low',
  1: 'AI processor not responding',
  2: 'LoRaWAN error',
  3: 'Restarted by its watchdog',
  4: 'Restarted after a brownout',
  5: 'Restarted after a firmware fault',
  8: 'Main camera failed',
  9: 'HM0360 camera failed',
  10: 'LED flash failed',
  11: 'SD card missing',
  12: 'Microphone failed',
  13: 'AI model failed',
  14: 'AI processor missed the BLE processor at start-up',
}

// Why the camera last restarted, for `unexpected_restart_<reason>` (WW500_LoRaWAN_payloads.md
// section 2; the reasons that are no warning never reach here).
const RESTART_REASONS: Record<number, string> = {
  2: 'Restarted after a firmware error check',
  3: 'Restarted after an SDK assert',
  4: 'Restarted after a radio stack assert',
  5: 'Restarted after a hard fault',
  6: 'Restarted by its watchdog',
  10: 'Restarted after a software reset or lockup',
}

/** One of `health_reasons` (ww-backend DATABASE_REFERENCE.md, Camera health) in words. */
export function healthReasonText(code: string): string {
  if (code === 'sd_low') return 'SD card nearly full'
  if (code === 'ai_failures_rose') return 'AI processor did not respond'
  const bit = /^self_test_bit_(\d+)$/.exec(code)
  if (bit) return SELF_TEST_BITS[Number(bit[1])] ?? `Self-test bit ${bit[1]}`
  const restart = /^unexpected_restart_(\d+)$/.exec(code)
  if (restart) return RESTART_REASONS[Number(restart[1])] ?? `Restarted unexpectedly (reason ${restart[1]})`
  return code
}

/** The reasons in words, worst first as the view gives them, each once. */
export function healthReasonTexts(codes: readonly string[] | null | undefined): string[] {
  return [...new Set((codes ?? []).map(healthReasonText))]
}

export const LOW_BATTERY_REASON = 'self_test_bit_0'
export const SD_LOW_REASON = 'sd_low'
