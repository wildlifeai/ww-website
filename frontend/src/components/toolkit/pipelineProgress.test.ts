import { describe, expect, it } from 'vitest'
import type { PipelineJob, PipelineState } from './PipelineStatusBox'
import { aiBatchWait, aiNotRun, aiNotRunReason, jobChanged } from './pipelineProgress'

const WAIT = { queued_at: '2026-11-22T01:00:00+00:00', usual_wait_minutes: 300 }

const drive = (status: string): PipelineJob => ({ id: `drive-${status}`, status, progress: 1, fileCount: 10 })
const ai = (status: string, batchWait: PipelineJob['batchWait'] = null): PipelineJob => ({ id: `ai-${status}`, status, progress: 0, fileCount: 0, batchWait })
const state = (jobs: PipelineJob[]): PipelineState => ({ totalFiles: 10, uploadedFiles: 10, jobs, logs: [], lastUpdateTs: 0 })

describe('aiBatchWait', () => {
  it('is the wait while the AI job waits for the GPU batch', () => {
    expect(aiBatchWait(state([drive('completed'), ai('queued', WAIT)]))).toEqual(WAIT)
  })

  it('is null on Azure, once the run starts, and while another AI job runs', () => {
    expect(aiBatchWait(state([drive('completed'), ai('queued')]))).toBeNull()
    expect(aiBatchWait(state([ai('processing')]))).toBeNull()
    expect(aiBatchWait(state([ai('queued', WAIT), ai('processing')]))).toBeNull()
    expect(aiBatchWait(state([drive('processing')]))).toBeNull()
  })
})

describe('jobChanged', () => {
  it('counts a queued job that starts waiting for the GPU batch as a change', () => {
    expect(jobChanged(ai('queued'), ai('queued', WAIT))).toBe(true)
    expect(jobChanged(ai('queued', WAIT), ai('queued', WAIT))).toBe(false)
    expect(jobChanged(ai('queued'), ai('queued'))).toBe(false)
    expect(jobChanged(ai('queued'), { ...ai('queued'), gpuBatch: true })).toBe(true)
  })

  it('counts status, progress, message and new events', () => {
    expect(jobChanged(ai('queued'), ai('processing'))).toBe(true)
    expect(jobChanged(ai('processing'), { ...ai('processing'), progress: 0.5 })).toBe(true)
    expect(jobChanged(ai('processing'), { ...ai('processing'), message: 'GPU cluster: 5 of 10 photos' })).toBe(true)
    expect(jobChanged(ai('processing'), { ...ai('processing'), eventCount: 1 })).toBe(true)
  })

  it('counts an upload job that reports the AI was not run', () => {
    expect(jobChanged(drive('completed'), { ...drive('completed'), aiNotRun: 'turned off for this upload' })).toBe(true)
    expect(jobChanged({ ...drive('completed'), aiNotRun: null }, drive('completed'))).toBe(false)
  })
})

describe('aiNotRunReason', () => {
  it("is the reason in an upload job's result when it did not run the AI (#432)", () => {
    expect(aiNotRunReason({ ai: 'not_run', ai_reason: 'no AI step enabled' })).toBe('no AI step enabled')
  })

  it('is null when the AI ran or was queued, and before the job has a result', () => {
    expect(aiNotRunReason({ ai: 'ran' })).toBeNull()
    expect(aiNotRunReason({ ai: 'queued', ai_job_id: 'a1b2c3d4' })).toBeNull()
    expect(aiNotRunReason(null)).toBeNull()
    expect(aiNotRunReason(undefined)).toBeNull()
  })
})

describe('aiNotRun', () => {
  const skipped = (reason: string): PipelineJob => ({ ...drive('completed'), aiNotRun: reason })

  it('is the reason once every upload job finished without running the AI', () => {
    expect(aiNotRun(state([skipped('turned off for this upload'), skipped('turned off for this upload')]))).toBe('turned off for this upload')
  })

  it('is null while an upload job is still running, or when one ran or queued the AI', () => {
    expect(aiNotRun(state([skipped('turned off for this upload'), drive('processing')]))).toBeNull()
    expect(aiNotRun(state([skipped('no photo matched a deployment'), drive('completed')]))).toBeNull()
    expect(aiNotRun(state([drive('completed'), ai('queued')]))).toBeNull()
    expect(aiNotRun(state([]))).toBeNull()
  })
})
