/**
 * Capture flash: when the camera lights its photos, with which LED, and the time-of-day window.
 *
 * projects.flash_mode, flash_led, flash_window_start_minutes_utc and flash_window_minutes
 * (ww-backend#168) reach the WW500 at deployment as op34, op13, op35 and op36, written by the
 * mobile app (ww-mobile-app#282). The device keeps UTC, so the window is stored as UTC minutes
 * after midnight plus a duration that may wrap past midnight; the panel edits it in the
 * browser's local time and shows the UTC it stores. The database defaults a project to 'off',
 * which also turns off the night IR for motion detection: the same gate arms both.
 */

export type FlashMode = 'off' | 'light_sensor' | 'always_on' | 'time_of_day'
export type FlashLed = 'white' | 'ir'

export const FLASH_MODES: { value: FlashMode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'light_sensor', label: 'Light sensor decides' },
  { value: 'always_on', label: 'Always on' },
  { value: 'time_of_day', label: 'Time of day' },
]

export const FLASH_LEDS: { value: FlashLed; label: string }[] = [
  { value: 'ir', label: 'IR' },
  { value: 'white', label: 'White' },
]

const DAY = 1440

/** Minutes east of UTC for the browser at `at`, e.g. +780 in New Zealand summer. */
export function localOffsetMinutes(at: Date = new Date()): number {
  return -at.getTimezoneOffset()
}

/** "19:30" → 1170. */
export function parseHHMM(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim())
  if (!m) return null
  const h = Number(m[1]), min = Number(m[2])
  return h < 24 && min < 60 ? h * 60 + min : null
}

/** 1170 → "19:30", wrapping into the day. */
export function formatHHMM(minutes: number): string {
  const m = ((minutes % DAY) + DAY) % DAY
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export interface FlashWindow {
  startUtc: number
  minutes: number
}

/** A local start and end ("19:00", "06:00") as the stored UTC window; end before start wraps past midnight. */
export function windowFromLocal(startLocal: string, endLocal: string, offset: number): FlashWindow | null {
  const start = parseHHMM(startLocal), end = parseHHMM(endLocal)
  if (start === null || end === null) return null
  const minutes = ((end - start) % DAY + DAY) % DAY || DAY // same time = the whole day
  return { startUtc: (((start - offset) % DAY) + DAY) % DAY, minutes }
}

/** The stored UTC window as local start and end times. */
export function windowToLocal(w: FlashWindow, offset: number): { start: string; end: string } {
  return { start: formatHHMM(w.startUtc + offset), end: formatHHMM(w.startUtc + offset + w.minutes) }
}

/** "06:00 to 17:00 UTC", what the device runs on. */
export function describeUtc(w: FlashWindow): string {
  return `${formatHHMM(w.startUtc)} to ${formatHHMM(w.startUtc + w.minutes)} UTC`
}

/** Filled in when a project switches to Time of day with no window yet: 18:00 to 06:00 local. */
export function defaultWindow(offset: number): FlashWindow {
  return windowFromLocal('18:00', '06:00', offset)!
}
