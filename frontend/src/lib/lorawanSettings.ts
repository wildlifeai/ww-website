/**
 * A project's LoRaWAN, set in Settings → Defaults (#524).
 *
 * projects.lorawan_required turns LoRaWAN on for the cameras deployed in the project, and
 * projects.lorawan_ping_minutes (ww-backend#408: 60 to 1440, default 240) is the check-in period
 * the mobile app writes to them as op 32 at deployment (ww-mobile-app#551). Nothing shorter than
 * 4 hours is offered: The Things Network's community network allows a device 10 downlinks a day,
 * each confirmed check-in's acknowledgement is one, and 4 hours uses 6, which leaves room for the
 * 3 settings requests a day the Realtime page may send (#521), which offers the same choices.
 */

export const LORAWAN_PING_MINUTES = { min: 60, max: 1440, default: 240 } as const

/** 4, 6, 8, 12 or 24 hours, in minutes. */
export const PING_CHOICES: readonly number[] = [4, 6, 8, 12, 24].map(h => h * 60)

/** The choices, plus `current` when it is a value the column allows but off the list. */
export function pingChoices(current?: number | null): number[] {
  const out = [...PING_CHOICES]
  if (current != null && !out.includes(current)) out.push(current)
  return out.sort((a, b) => a - b)
}

/** 240 → "4 hours", 60 → "1 hour", 90 → "90 minutes". */
export function formatPing(minutes: number): string {
  if (minutes % 60 !== 0) return `${minutes} minutes`
  const hours = minutes / 60
  return hours === 1 ? '1 hour' : `${hours} hours`
}
