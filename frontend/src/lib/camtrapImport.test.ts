import { describe, expect, it, vi } from 'vitest'
import { IMPORT_FAILED, importOutcome, startCamtrapImport } from './camtrapImport'
import type { JobInfo } from '../types/job'

function job(over: Partial<JobInfo>): JobInfo {
  return {
    job_id: 'job-1', status: 'processing', progress: 0.3, created_at: '', updated_at: null, result_url: null,
    error: null, message: null, current_phase: null, summary: null, events: [], event_count: 0, ...over,
  }
}

const RESULT = {
  project_id: 'p1', project_name: 'Test package', deployments_imported: 1, media_imported: 300,
  observations_imported: 300, warnings: ['1 of 1 deployment(s) have no GPS coordinates'], ai_job_id: null,
}

describe('startCamtrapImport', () => {
  it('sends the package and the AI choice, and returns the job id', async () => {
    const upload = vi.fn().mockResolvedValue({ data: { job_id: 'job-1', status: 'queued' } })
    const zip = new File(['PK'], 'pkg.zip', { type: 'application/zip' })

    expect(await startCamtrapImport(zip, true, upload)).toBe('job-1')
    const [path, form] = upload.mock.calls[0] as [string, FormData]
    expect(path).toBe('/api/camtrapdp/import')
    expect((form.get('file') as File).name).toBe('pkg.zip')
    expect(form.get('run_ai')).toBe('true')
  })

  it('says the import failed when no job comes back', async () => {
    const upload = vi.fn().mockResolvedValue({ data: {} })
    await expect(startCamtrapImport(new File(['PK'], 'pkg.zip'), false, upload)).rejects.toThrow(IMPORT_FAILED)
  })

  it('passes the server message on', async () => {
    const upload = vi.fn().mockRejectedValue(new Error('You must belong to an organisation to import data.'))
    await expect(startCamtrapImport(new File(['PK'], 'pkg.zip'), false, upload)).rejects.toThrow('organisation')
  })
})

describe('importOutcome', () => {
  it('is running until the job is read and while it works', () => {
    expect(importOutcome(undefined)).toEqual({ state: 'running' })
    expect(importOutcome(job({ status: 'queued' }))).toEqual({ state: 'running' })
    expect(importOutcome(job({ status: 'processing' }))).toEqual({ state: 'running' })
  })

  it('is done with the result, warnings or not', () => {
    expect(importOutcome(job({ status: 'completed', result: RESULT }))).toEqual({ state: 'done', result: RESULT })
    expect(importOutcome(job({ status: 'completed_with_errors', result: RESULT }))).toEqual({ state: 'done', result: RESULT })
  })

  it('fails with the job error, or a plain message without one', () => {
    expect(importOutcome(job({ status: 'failed', error: 'Uploaded file is not a valid ZIP archive.' })))
      .toEqual({ state: 'failed', error: 'Uploaded file is not a valid ZIP archive.' })
    expect(importOutcome(job({ status: 'failed' }))).toEqual({ state: 'failed', error: IMPORT_FAILED })
    expect(importOutcome(job({ status: 'completed' }))).toEqual({ state: 'failed', error: IMPORT_FAILED })
  })
})
