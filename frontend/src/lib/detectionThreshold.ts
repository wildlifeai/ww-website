/**
 * On-device detection threshold: how confident the camera's model must be before a photo
 * counts as a detection.
 *
 * projects.detection_threshold_pct (ww-backend#246) reaches the WW500 at deployment as op16
 * MODEL_THRESHOLD, written by the mobile app (ww-mobile-app#342). The range mirrors the column's
 * CHECK constraint and 57 its default. Both come from firmware before Seeed PR 289, whose op16 is
 * the model's raw int8 output (0 to 127, about 50% and up, factory setting 18, about 57%), and
 * which reads the 57 the app writes as about 72%. With PR 289 op16 is a percent from 0 to 100,
 * factory setting 60. Source: the op 16 row in
 * documentation/resources/AI-ARCHITECTURE.md#the-contracts-that-must-never-break
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
