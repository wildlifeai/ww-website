import { describe, expect, it } from 'vitest'
import type { JobInfo, JobStatusValue } from '../types/job'
import { inatSyncRunning, inatSyncText } from './inatSync'

const job = (status: JobStatusValue, over: Partial<JobInfo> = {}): JobInfo => ({
  job_id: 'j1',
  status,
  progress: 0,
  created_at: '2026-11-22T00:00:00Z',
  updated_at: null,
  result_url: null,
  error: null,
  message: null,
  current_phase: null,
  summary: null,
  events: [],
  event_count: 0,
  ...over,
})

describe('the iNaturalist sync job', () => {
  it('runs until the job is read and finished', () => {
    expect(inatSyncRunning(undefined)).toBe(true)
    expect(inatSyncRunning(job('queued'))).toBe(true)
    expect(inatSyncRunning(job('processing'))).toBe(true)
    expect(inatSyncRunning(job('completed'))).toBe(false)
    expect(inatSyncRunning(job('failed'))).toBe(false)
  })

  it('shows the progress, then the counts the job ends with', () => {
    expect(inatSyncText(undefined)).toBe('Starting the sync…')
    expect(inatSyncText(job('queued'))).toBe('Syncing…')
    expect(inatSyncText(job('processing', { message: 'Checked 40 of 200 observations…' }))).toBe('Checked 40 of 200 observations…')
    const done = 'Synced 2 of 3: 1 research grade, 0 disagreement, 0 community IDs written.'
    expect(inatSyncText(job('completed', { message: done }))).toBe(done)
  })

  it('says why it failed', () => {
    expect(inatSyncText(job('failed', { error: 'Failed to batch fetch observations' }))).toBe('Sync failed: Failed to batch fetch observations')
    expect(inatSyncText(job('failed'))).toBe('Sync failed: try again later.')
  })
})
