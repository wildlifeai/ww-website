// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useCamtrapImport: send a CamtrapDP package, follow its import job, and call `onImported`
// once when it is done. The rules live in lib/camtrapImport.ts; UploadFlow shows the state.
import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { IMPORT_FAILED, importOutcome, startCamtrapImport } from '../lib/camtrapImport'
import { useJob } from './useJob'

export function useCamtrapImport(onImported: () => void) {
  const [jobId, setJobId] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const { data: job } = useJob(jobId)
  const outcome = jobId ? importOutcome(job) : null
  const announced = useRef<string | null>(null)

  const done = outcome?.state === 'done'
  useEffect(() => {
    if (done && jobId && announced.current !== jobId) {
      announced.current = jobId
      onImported()
    }
  }, [done, jobId, onImported])

  const start = async (zip: File, runAi: boolean) => {
    setSending(true); setSendError(null); setJobId(null)
    try {
      setJobId(await startCamtrapImport(zip, runAi, apiClient.upload))
    } catch (err) {
      setSendError(err instanceof Error ? err.message : IMPORT_FAILED)
    } finally {
      setSending(false)
    }
  }

  const reset = () => { setJobId(null); setSendError(null) }

  return {
    start,
    reset,
    /** The package is on its way and no job exists yet: leaving the page loses it. */
    sending,
    /** The job runs on the server, whether or not this page stays open. */
    running: outcome?.state === 'running',
    progress: job?.progress ?? 0,
    message: job?.message ?? null,
    result: outcome?.state === 'done' ? outcome.result : null,
    error: sendError ?? (outcome?.state === 'failed' ? outcome.error : null),
  }
}
