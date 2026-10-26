import { describe, expect, it } from 'vitest'
import type { JobSummary } from '../types/job'
import { anyRunning, JOB_STATUS_FILTERS, jobsListPath } from './jobsList'

describe('jobsListPath', () => {
  it('asks for one page of every status', () => {
    expect(jobsListPath(50)).toBe('/api/jobs?limit=50')
  })

  it('repeats status and passes the cursor encoded', () => {
    const before = '2026-10-20T04:00:00.500000Z|00000000-0000-4000-8000-000000000006'
    const path = jobsListPath(50, ['queued', 'processing'], before)
    const q = new URLSearchParams(path.split('?')[1])
    expect(q.getAll('status')).toEqual(['queued', 'processing'])
    expect(q.get('before')).toBe(before)
    expect(path).not.toContain('|')
  })
})

describe('JOB_STATUS_FILTERS', () => {
  it('reads every status for all, and both live ones for running', () => {
    expect(JOB_STATUS_FILTERS.find(f => f.value === '')?.statuses).toEqual([])
    expect(JOB_STATUS_FILTERS.find(f => f.value === 'running')?.statuses).toEqual(['queued', 'processing'])
  })
})

describe('anyRunning', () => {
  const job = (status: JobSummary['status']) => ({ status }) as JobSummary
  it('is true while a job is queued or processing', () => {
    expect(anyRunning([job('completed'), job('queued')])).toBe(true)
    expect(anyRunning([job('processing')])).toBe(true)
    expect(anyRunning([job('completed'), job('failed'), job('completed_with_errors')])).toBe(false)
    expect(anyRunning([])).toBe(false)
  })
})
