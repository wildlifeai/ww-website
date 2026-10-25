// The annotation state a photo's badge shows (components/ui/StatusBadge), derived from its
// observations. Kept out of the component file so that file exports components only.

export type AnnotationStatus = 'pending' | 'issue' | 'ai' | 'reviewed'

/**
 * A photo's AnnotationStatus from its observations. `review_status` is authoritative (AN-2, see
 * lib/observations.ts):
 *   - `hasReviewed`: any observation a human has validated
 *     (review_status human_reviewed, expert_reviewed or consensus_approved)
 *   - `hasAi`: any observation carrying an AI-produced label
 *   - `hasError`: an explicit pipeline error flag (reserved for BE-2)
 *
 * Precedence: error, then reviewed, then ai, then pending. With no observations at all the
 * photo is still going through the pipeline, so 'pending' (a neutral badge), not a red error.
 * Derive `hasReviewed` and `hasAi` with `isHumanReviewed` and `isAiLabel` from
 * lib/observations.ts so every surface agrees.
 */
export function deriveAnnotationStatus({
  hasReviewed,
  hasAi,
  hasError = false,
}: {
  hasReviewed: boolean
  hasAi: boolean
  hasError?: boolean
}): AnnotationStatus {
  if (hasError) return 'issue'
  if (hasReviewed) return 'reviewed'
  if (hasAi) return 'ai'
  return 'pending'
}
