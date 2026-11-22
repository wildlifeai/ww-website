// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Import a CamtrapDP package (#454). POST /api/camtrapdp/import keeps the package (on an
// S3-backed server the browser sends it to the bucket first, lib/objectStoreUpload.ts) and
// answers a job; the job's `result` holds the counts and warnings once it completes.
// useCamtrapImport follows the job; UploadFlow shows it.
import type { JobInfo } from '../types/job'

export interface CamtrapImportResult {
  project_id: string
  project_name?: string
  deployments_imported: number
  media_imported: number
  observations_imported: number
  warnings: string[]
  /** Set when run_ai enqueued a SpeciesNet + Wildlife Brain job for the bundled images. */
  ai_job_id?: string | null
}

export const IMPORT_FAILED = 'The import failed. Try again.'

type Upload = (path: string, form: FormData) => Promise<unknown>

/** Send the package and start the import; returns the job id. Throws an Error with the message to show. */
export async function startCamtrapImport(zip: File, runAi: boolean, upload: Upload): Promise<string> {
  const form = new FormData()
  form.append('file', zip)
  form.append('run_ai', String(runAi))
  const res = await upload('/api/camtrapdp/import', form) as { data?: { job_id?: string } } | null
  const jobId = res?.data?.job_id
  if (!jobId) throw new Error(IMPORT_FAILED)
  return jobId
}

export type ImportOutcome =
  | { state: 'running' }
  | { state: 'done'; result: CamtrapImportResult }
  | { state: 'failed'; error: string }

/** Where an import job stands; a job not read yet is running. */
export function importOutcome(job: JobInfo | undefined): ImportOutcome {
  if (job?.status === 'failed') return { state: 'failed', error: job.error || IMPORT_FAILED }
  if (job?.status === 'completed' || job?.status === 'completed_with_errors') {
    const result = job.result as CamtrapImportResult | null | undefined
    if (!result) return { state: 'failed', error: IMPORT_FAILED }
    return { state: 'done', result: { ...result, warnings: result.warnings ?? [] } }
  }
  return { state: 'running' }
}
