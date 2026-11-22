// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// What to say while AI or grouping waits for the GPU batch (#460). On AWS a GPU job waits in
// the queue for the next run on the REANNZ cluster. The job API sets `batch_wait` only while a
// job waits, with `usual_wait_minutes` worked out from the batch and cron settings
// (`store.usual_batch_wait_minutes`), so on Azure, where the work runs at once, the wording stays
// as it was. Every place that states the wait words it with `batchWaitText`.
import type { BatchWait, JobSummary } from '../types/job'

/** A wait in words: "an hour", "2 hours", "45 minutes". */
export function waitInWords(minutes: number): string {
  if (minutes === 60) return 'an hour'
  if (minutes > 60 && minutes % 60 === 0) return `${minutes / 60} hours`
  return minutes === 1 ? 'a minute' : `${minutes} minutes`
}

/** The one line shown while a job waits for the GPU batch. */
export function batchWaitText(wait: BatchWait): string {
  const text = 'Waiting for the next GPU run'
  return wait.usual_wait_minutes > 0 ? `${text}, usually within ${waitInWords(wait.usual_wait_minutes)}` : text
}

/** Job kinds whose run writes a deployment's clusters: Run Wildlife Brain, a reprocess, and the
 *  clustering that follows an AI run on the batch (the backend's `create_job` kinds). */
const GROUPING_KINDS = new Set(['ai_embed', 'ai_reprocess', 'embedding'])

/** The newest grouping job on the deployment that has not finished, from a newest-first list. */
export function pendingGrouping(jobs: readonly JobSummary[], deploymentId: string | undefined): JobSummary | undefined {
  if (!deploymentId) return undefined
  return jobs.find(j =>
    (j.status === 'queued' || j.status === 'processing')
    && GROUPING_KINDS.has(j.kind ?? '')
    && j.deployment_ids.includes(deploymentId))
}

/** Run Wildlife Brain is offered only with no run starting, waiting or running on the deployment,
 *  so a run that waits for the GPU batch is never queued twice. */
export function canStartGrouping(deploymentId: string | undefined, starting: boolean, pending: JobSummary | undefined): boolean {
  return !!deploymentId && !starting && !pending
}

/** The Clusters page's line for a grouping still to finish: the batch wait, else the wording for
 *  work that runs at once. `startedJobId` is the job this visit started, before the list has it. */
export function groupingStatusText(pending: JobSummary | undefined, startedJobId: string | undefined): string | null {
  if (pending?.batch_wait) return `${batchWaitText(pending.batch_wait)}.`
  const jobId = pending?.job_id ?? startedJobId
  return jobId ? `Queued (job ${jobId.slice(0, 8)}). Refresh shortly.` : null
}
