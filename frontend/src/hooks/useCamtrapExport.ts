// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useCamtrapExport: start a CamtrapDP export, follow its job, and download the ZIP once.
// The rules live in lib/camtrapExport.ts; CamtrapExportStatus shows the state.
import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { supabase } from '../config/supabase'
import { EXPORT_FAILED, saveFile, startCamtrapExport } from '../lib/camtrapExport'
import { useJob } from './useJob'

export function useCamtrapExport() {
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloaded, setDownloaded] = useState(false)
  const saved = useRef<string | null>(null)
  const { data: job } = useJob(jobId)

  const done = job?.status === 'completed' || job?.status === 'completed_with_errors'
  useEffect(() => {
    if (done && job?.result_url && saved.current !== job.job_id) {
      saved.current = job.job_id
      saveFile(job.result_url)
    }
  }, [done, job])

  const running = starting || (!!jobId && (!job || job.status === 'queued' || job.status === 'processing'))

  const start = async (projectIds: string[]) => {
    setStarting(true); setError(null); setDownloaded(false); setJobId(null)
    try {
      const started = await startCamtrapExport(projectIds, apiClient.post, supabase)
      if (started.kind === 'job') setJobId(started.jobId)
      else setDownloaded(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : EXPORT_FAILED)
    } finally {
      setStarting(false)
    }
  }

  return { start, running, error, job: jobId ? job ?? null : null, downloaded }
}
