// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/** Pure helpers over PipelineState — kept out of the component file so they can be shared
 *  (e.g. the Insights live banner) without tripping react-refresh's component-only rule. */
import type { BatchWait } from '../../types/job'
import type { PipelineJob, PipelineState } from './PipelineStatusBox'

const TERMINAL_STATUSES = ['completed', 'completed_with_errors', 'failed', 'skipped']

/**
 * The offloaded AI-analysis job(s). The upload spawns one via `child_job_id` and the dock
 * chains it in with `fileCount: 0` (it processes deployments, not a file count) — so a
 * 0-fileCount job is the analysis phase. Returns its aggregate progress and whether it's
 * still running: drives the "Analysing…" phase of the dock indicator and the Insights banner.
 */
export function aiAnalysisStatus(state: PipelineState): { active: boolean; progress: number } | null {
  const ai = state.jobs.filter((j) => j.fileCount === 0)
  if (ai.length === 0) return null
  const active = ai.some((j) => !TERMINAL_STATUSES.includes(j.status))
  const progress = ai.reduce((s, j) => s + Math.max(0, Math.min(1, j.progress || 0)), 0) / ai.length
  return { active, progress }
}

/**
 * The GPU batch wait while every running AI job waits for it (AWS, #460), else null. The dock
 * then says the analysis waits for the next GPU run instead of showing it at 0%.
 */
export function aiBatchWait(state: PipelineState): BatchWait | null {
  const running = state.jobs.filter((j) => j.fileCount === 0 && !TERMINAL_STATUSES.includes(j.status))
  if (running.length === 0 || !running.every((j) => j.batchWait)) return null
  return running[0].batchWait ?? null
}

/**
 * True when a poll changed what the dock shows for a job. A job that starts waiting for the GPU
 * batch stays queued, so the wait and the batch flag count as changes themselves.
 */
export function jobChanged(prev: PipelineJob, next: PipelineJob): boolean {
  return (
    prev.status !== next.status ||
    prev.progress !== next.progress ||
    prev.message !== next.message ||
    (next.eventCount ?? 0) > (prev.eventCount ?? 0) ||
    !prev.batchWait !== !next.batchWait ||
    !prev.gpuBatch !== !next.gpuBatch
  )
}
