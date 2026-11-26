import { describe, expect, it } from 'vitest'
import { derivePhase } from './uploadPhase'
import type { PipelineState } from '../components/toolkit/PipelineStatusBox'

function state(partial: Partial<PipelineState>): PipelineState {
  return {
    totalFiles: 0,
    uploadedFiles: 0,
    failedFiles: 0,
    jobs: [],
    logs: [],
    lastUpdateTs: Date.now(),
    uploadError: null,
    ...partial,
  }
}

function job(status: string, fileCount = 10) {
  return { id: `job-${status}`, status, progress: 0.5, fileCount }
}

describe('derivePhase', () => {
  it('is idle before anything starts', () => {
    expect(derivePhase(state({}))).toBe('idle')
  })

  it('is uploading while files are unaccounted for', () => {
    expect(derivePhase(state({ totalFiles: 150, uploadedFiles: 40 }))).toBe('uploading')
  })

  it('stays uploading when some batches failed but others are still pending', () => {
    expect(derivePhase(state({ totalFiles: 150, uploadedFiles: 40, failedFiles: 10 }))).toBe(
      'uploading',
    )
  })

  // The 26 Jul production incident: every batch "succeeded" in seconds while
  // nothing was stored. These pin the states that must never read as success.
  describe('no false success', () => {
    it('fails when every file settled and any failed (total batch failure)', () => {
      expect(derivePhase(state({ totalFiles: 150, uploadedFiles: 0, failedFiles: 150 }))).toBe(
        'failed',
      )
    })

    it('fails after the storage-refused abort (sent batch + remainder failed)', () => {
      // Batch 1 returned enabled:false → uploadedFiles=10, the other 140 are
      // counted failed and the loop breaks (see UploadContext).
      expect(
        derivePhase(
          state({
            totalFiles: 150,
            uploadedFiles: 10,
            failedFiles: 140,
            uploadError: 'Image storage is not configured on this server',
          }),
        ),
      ).toBe('failed')
    })

    it('fails on uploadError when no job was ever created', () => {
      expect(
        derivePhase(
          state({ totalFiles: 10, uploadedFiles: 10, uploadError: 'Azure buffer unreachable' }),
        ),
      ).toBe('failed')
    })

    it('fails when any job failed, even with others complete', () => {
      expect(
        derivePhase(state({ totalFiles: 20, uploadedFiles: 20, jobs: [job('completed'), job('failed')] })),
      ).toBe('failed')
    })
  })

  it('completes when all files uploaded and no jobs were needed (all duplicates)', () => {
    expect(derivePhase(state({ totalFiles: 10, uploadedFiles: 10 }))).toBe('completed')
  })

  it('completes when every job reached a terminal state', () => {
    expect(
      derivePhase(
        state({
          totalFiles: 20,
          uploadedFiles: 20,
          jobs: [job('completed'), job('completed_with_errors'), job('skipped')],
        }),
      ),
    ).toBe('completed')
  })

  it('is processing while jobs run and uploads are done', () => {
    expect(
      derivePhase(state({ totalFiles: 20, uploadedFiles: 20, jobs: [job('processing')] })),
    ).toBe('processing')
  })

  it('stalls when a processing job has been silent for over 15s', () => {
    expect(
      derivePhase(
        state({
          totalFiles: 20,
          uploadedFiles: 20,
          jobs: [job('processing')],
          lastUpdateTs: Date.now() - 16_000,
        }),
      ),
    ).toBe('stalled')
  })

  it('never stalls on a job of the GPU batch, which reports every few minutes (#460)', () => {
    const silent = { totalFiles: 20, uploadedFiles: 20, lastUpdateTs: Date.now() - 10 * 60_000 }
    const wait = { queued_at: '2026-11-22T01:00:00+00:00', usual_wait_minutes: 300 }
    const waiting = { ...job('queued', 0), gpuBatch: true, batchWait: wait }
    const onCluster = { ...job('processing', 0), gpuBatch: true }
    expect(derivePhase(state({ ...silent, jobs: [job('completed'), waiting] }))).toBe('processing')
    expect(derivePhase(state({ ...silent, jobs: [job('completed'), onCluster] }))).toBe('processing')
    expect(derivePhase(state({ ...silent, jobs: [job('completed'), job('processing', 0)] }))).toBe('stalled')
  })
})
