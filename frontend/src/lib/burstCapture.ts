/**
 * Burst capture: how many photos the camera takes on every trigger, and the gap between them.
 *
 * projects.pictures_per_trigger and projects.picture_interval_ms (ww-backend#218) reach the
 * WW500 at deployment as op5 NUM_PICTURES and op6 PICTURE_INTERVAL, written by the mobile app
 * (ww-mobile-app#317). The count is photos as the user sees them: when the project also records
 * the raw BMP, the app doubles it for op5. The ranges mirror the columns' CHECK constraints;
 * 2000 ms is the HM0360's limit.
 */

export const PHOTOS_PER_TRIGGER = { min: 1, max: 10, default: 3 } as const
export const PHOTO_INTERVAL_MS = { min: 200, max: 2000, step: 100, default: 1000 } as const

/** 1 to 10. */
export function photoCountOptions(): number[] {
  const out: number[] = []
  for (let n = PHOTOS_PER_TRIGGER.min; n <= PHOTOS_PER_TRIGGER.max; n++) out.push(n)
  return out
}

/** 200 to 2000 ms in 100 ms steps, plus the current value when it is off that grid. */
export function photoIntervalOptions(current?: number | null): number[] {
  const out: number[] = []
  for (let ms = PHOTO_INTERVAL_MS.min; ms <= PHOTO_INTERVAL_MS.max; ms += PHOTO_INTERVAL_MS.step) out.push(ms)
  if (current != null && !out.includes(current)) out.push(current)
  return out.sort((a, b) => a - b)
}

/** 1000 → "1 s", 200 → "0.2 s", 1250 → "1.25 s". */
export function formatInterval(ms: number): string {
  return `${Number((ms / 1000).toFixed(2))} s`
}

/** The cost of a burst, shown next to the control; null for a single photo. */
export function burstCostNote(photos: number): string | null {
  if (photos <= 1) return null
  return `Each trigger uses about ${photos}× the card space and battery of a single photo.`
}
