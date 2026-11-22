// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// POST /api/inat/sync starts a job (backend/app/jobs/inat_sync.py) and answers with its id, so a
// sync of 200 observations does not hold a request past API Gateway's 30 s (#455). Settings,
// Integrations follows the job with useJob; these say what it shows while it runs and after.
import type { JobInfo } from '../types/job'

/** True until the job has finished, failed included; an unread job counts as running. */
export function inatSyncRunning(job: JobInfo | undefined): boolean {
  return !job || job.status === 'queued' || job.status === 'processing'
}

/** The panel's line for the sync job: its progress, the counts it ends with, or why it failed. */
export function inatSyncText(job: JobInfo | undefined): string {
  if (!job) return 'Starting the sync…'
  if (job.status === 'failed') return `Sync failed: ${job.error ?? 'try again later.'}`
  return job.message ?? 'Syncing…'
}
