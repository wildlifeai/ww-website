// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GET /api/jobs, the signed-in user's jobs a page at a time (UI audit 01 E1). The status filter
// runs in the read, and "Load more" passes the previous page's `next_before` as `before`.
import type { JobStatusValue, JobSummary } from '../types/job'

/** The status filter's choices on Processing history; "running" covers queued and processing. */
export const JOB_STATUS_FILTERS: { value: string; label: string; statuses: JobStatusValue[] }[] = [
  { value: '', label: 'All statuses', statuses: [] },
  { value: 'running', label: 'Running', statuses: ['queued', 'processing'] },
  { value: 'completed', label: 'Completed', statuses: ['completed'] },
  { value: 'completed_with_errors', label: 'With errors', statuses: ['completed_with_errors'] },
  { value: 'failed', label: 'Failed', statuses: ['failed'] },
]

/** One page's path: `limit` jobs in `statuses` (all when empty), after the `before` cursor. */
export function jobsListPath(limit: number, statuses: readonly JobStatusValue[] = [], before: string | null = null): string {
  const q = new URLSearchParams({ limit: String(limit) })
  for (const s of statuses) q.append('status', s)
  if (before) q.set('before', before)
  return `/api/jobs?${q}`
}

/** True while any of the jobs is queued or processing. */
export function anyRunning(jobs: readonly JobSummary[]): boolean {
  return jobs.some(j => j.status === 'queued' || j.status === 'processing')
}
