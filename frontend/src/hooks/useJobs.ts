// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useJobsList, the signed-in user's recent processing jobs (GET /api/jobs).
// Backs the avatar-menu "Processing history" view. Polls while any job is still
// running so the list updates live, then stops once everything is terminal.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import type { JobSummary } from '../types/job'

/** Jobs per "Load more" on Processing history. */
export const JOBS_PAGE = 50
/** The most GET /api/jobs returns (`limit` le=200 in routers/jobs.py). */
export const JOBS_API_MAX = 200

export function useJobsList(limit = JOBS_PAGE) {
  return useQuery({
    queryKey: ['jobs', 'list', limit],
    queryFn: async (): Promise<JobSummary[]> => {
      const r = (await apiClient.get(`/api/jobs?limit=${limit}`)) as any
      return (r.data?.jobs ?? []) as JobSummary[]
    },
    // Keep the rows on screen while "Load more" reads the longer list.
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      // Poll fast while something is running so the dock + the Annotations
      // "being processed" banner update live; keep a slow idle poll so a
      // newly-started job is picked up within a few seconds.
      const jobs = query.state.data as JobSummary[] | undefined
      const active = jobs?.some(j => j.status === 'queued' || j.status === 'processing')
      return active ? 3000 : 15000
    },
  })
}
