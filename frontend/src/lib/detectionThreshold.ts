/**
 * On-device detection threshold: how confident the camera's model must be before a photo
 * counts as a detection.
 *
 * projects.detection_threshold_pct (ww-backend#246) reaches the WW500 at deployment as op16
 * MODEL_THRESHOLD, written by the mobile app (ww-mobile-app#342). The range mirrors the column's
 * CHECK constraint; 50 is the lowest the camera can do, and 57 is its factory setting.
 */

export const DETECTION_THRESHOLD_PCT = { min: 50, max: 99, default: 57 } as const

/**
 * What the number input saves: a whole percent inside 50 to 99, or `current` when the input is
 * empty or not a number.
 */
export function clampThreshold(input: string, current: number): number {
  const n = Number(input)
  if (input.trim() === '' || !Number.isFinite(n)) return current
  return Math.min(DETECTION_THRESHOLD_PCT.max, Math.max(DETECTION_THRESHOLD_PCT.min, Math.round(n)))
}
