/**
 * Pure phase derivation for the upload pipeline.
 *
 * Lives outside UploadContext so it is unit-testable without pulling in
 * React or the API client, and so there is exactly one implementation —
 * AnalyseImages used to carry its own copy, which drifted and missed the
 * failedFiles handling (a failed batch rendered as "uploading" forever).
 */
import type { PipelineState } from '../components/toolkit/PipelineStatusBox'

export type UploadPhase =
  | 'idle'
  | 'uploading'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'stalled'

export function derivePhase(state: PipelineState): UploadPhase {
  if (state.jobs.some((j) => j.status === 'failed')) return 'failed'

  // A batch that threw is a failed run, not a silent one. Without this a total
  // upload failure rendered as "Pipeline Complete 100%" with the error buried
  // in the collapsed technical log (bench, production, 26 Jul).
  const failed = state.failedFiles ?? 0
  if (failed > 0 && failed + state.uploadedFiles >= state.totalFiles) return 'failed'
  if (state.uploadError && state.jobs.length === 0) return 'failed'

  if (
    state.jobs.length > 0 &&
    state.jobs.every((j) =>
      ['completed', 'completed_with_errors', 'failed', 'skipped'].includes(j.status),
    )
  )
    return 'completed'

  // Edge case: all files were duplicates — no jobs enqueued but upload finished
  if (
    state.totalFiles > 0 &&
    state.uploadedFiles === state.totalFiles &&
    state.jobs.length === 0
  )
    return 'completed'

  // Only files still unaccounted for (neither uploaded nor failed) mean "uploading"
  if (state.uploadedFiles + failed < state.totalFiles) return 'uploading'

  // A job on the GPU batch (AWS) reports every few minutes, waiting or running on the cluster,
  // so 15 s of silence is normal for it; the backend requeues a run that stops reporting (#460).
  const idleMs = Date.now() - state.lastUpdateTs
  if (idleMs > 15_000 && state.jobs.some((j) => j.status === 'processing' && !j.gpuBatch)) return 'stalled'

  if (state.jobs.length > 0) return 'processing'
  return 'idle'
}
