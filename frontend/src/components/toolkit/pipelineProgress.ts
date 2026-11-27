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

/** Why an upload job did not run the AI, from its `result` (`{ ai: 'not_run', ai_reason }`, #432), else null. */
export function aiNotRunReason(result: Record<string, unknown> | null | undefined): string | null {
  return result?.ai === 'not_run' ? String(result.ai_reason ?? '') : null
}

/**
 * Why the upload's AI was not run, once every upload job has finished without running it, else
 * null: the dock then says so instead of "Species identified".
 */
export function aiNotRun(state: PipelineState): string | null {
  const uploads = state.jobs.filter((j) => j.fileCount > 0)
  return uploads.length > 0 && uploads.every((j) => j.aiNotRun != null) ? uploads[0].aiNotRun ?? null : null
}

/**
 * True when a poll changed what the dock shows for a job. A job that starts waiting for the GPU
 * batch stays queued, so the wait and the batch flag count as changes themselves.
 */
export function jobChanged(prev: PipelineJob, next: PipelineJob): boolean {
  return (
    prev.status !== next.status ||
    prev.serverStatus !== next.serverStatus ||
    (prev.summary?.uploaded ?? null) !== (next.summary?.uploaded ?? null) ||
    prev.progress !== next.progress ||
    prev.message !== next.message ||
    (next.eventCount ?? 0) > (prev.eventCount ?? 0) ||
    !prev.batchWait !== !next.batchWait ||
    !prev.gpuBatch !== !next.gpuBatch ||
    (prev.aiNotRun ?? null) !== (next.aiNotRun ?? null)
  )
}

/** Whether the server has finished with a job, by its own status, not the dock's 99.9% shortcut. */
export function serverDone(job: PipelineJob): boolean {
  return TERMINAL_STATUSES.includes(job.serverStatus ?? job.status)
}

/**
 * Every photo the browser sent is stored on the server: all of them uploaded, none failed, and
 * every upload job finished on the server without a failure. The SD card can come out, and the
 * grid's "uploading" cards have their rows, even while the AI still waits for the next GPU run.
 */
export function uploadsStored(state: PipelineState): boolean {
  const uploads = state.jobs.filter((j) => j.fileCount > 0)
  return state.totalFiles > 0
    && state.uploadedFiles >= state.totalFiles
    && (state.failedFiles ?? 0) === 0
    && !state.uploadError
    && uploads.length > 0
    && uploads.every((j) => serverDone(j) && (j.serverStatus ?? j.status) !== 'failed' && (j.summary?.failed ?? 0) === 0)
}
