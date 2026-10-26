// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The signed-in user's processing jobs (GET /api/jobs). useJobsList reads the latest page for
// the Annotations "being processed" banner and the thumbnail retry; useJobHistory backs the
// avatar-menu "Processing history" view, filtered by status in the read and a page at a time.
// Both poll while any job is still running so they update live, then slow down once
// everything is terminal.
import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { anyRunning, jobsListPath } from '../lib/jobsList'
import type { JobStatusValue, JobSummary, JobsPage } from '../types/job'

/** Jobs per page. */
export const JOBS_PAGE = 50

// Poll fast while something is running so the banner and the history update live; keep a
// slow idle poll so a newly-started job is picked up within a few seconds.
const pollEvery = (jobs: readonly JobSummary[]) => (anyRunning(jobs) ? 3000 : 15000)

async function readPage(statuses: readonly JobStatusValue[], before: string | null): Promise<JobsPage> {
  const r = await apiClient.get(jobsListPath(JOBS_PAGE, statuses, before))
  return { jobs: r.data?.jobs ?? [], next_before: r.data?.next_before ?? null }
}

/** The user's latest jobs, newest first. */
export function useJobsList() {
  return useQuery({
    queryKey: ['jobs', 'list'],
    queryFn: async (): Promise<JobSummary[]> => (await readPage([], null)).jobs,
    refetchInterval: query => pollEvery(query.state.data ?? []),
  })
}

/** The user's jobs in `statuses` (all when empty), newest first; `fetchNextPage` reads the next page. */
export function useJobHistory(statuses: readonly JobStatusValue[]) {
  return useInfiniteQuery({
    queryKey: ['jobs', 'history', statuses],
    queryFn: ({ pageParam }) => readPage(statuses, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: last => last.next_before,
    // Keep the rows on screen while a filter change reads the new set.
    placeholderData: keepPreviousData,
    refetchInterval: query => pollEvery(query.state.data?.pages.flatMap(p => p.jobs) ?? []),
  })
}
