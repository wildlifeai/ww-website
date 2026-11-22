import { describe, expect, it } from 'vitest'
import type { PipelineJob, PipelineState } from './PipelineStatusBox'
import { aiBatchWait, jobChanged } from './pipelineProgress'

const WAIT = { queued_at: '2026-11-22T01:00:00+00:00', usual_wait_minutes: 120 }

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
})
