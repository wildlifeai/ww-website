import { describe, expect, it } from 'vitest'
import { THUMBNAIL_GRACE_MS, busyDeployments, isThumbnailStuck } from './thumbnailRetry'

const DEP = 'a15e8ed9-daa9-4a03-9823-5283e1cc6ace'
const NOW = Date.UTC(2026, 9, 2, 9, 0, 0)
const ago = (ms: number) => new Date(NOW - ms).toISOString()
const none = new Set<string>()

describe('isThumbnailStuck', () => {
  it('is stuck once the grace period has passed with no job running', () => {
    expect(isThumbnailStuck({ deployment_id: DEP, created_at: ago(THUMBNAIL_GRACE_MS + 1) }, NOW, none)).toBe(true)
  })

  it('is still processing inside the grace period', () => {
    expect(isThumbnailStuck({ deployment_id: DEP, created_at: ago(60_000) }, NOW, none)).toBe(false)
  })

  it('is still processing while a job covers the deployment, however old the photo', () => {
    expect(isThumbnailStuck({ deployment_id: DEP, created_at: ago(86_400_000) }, NOW, new Set([DEP]))).toBe(false)
  })

  it('treats a photo with no creation time as old', () => {
    expect(isThumbnailStuck({ deployment_id: DEP, created_at: null }, NOW, none)).toBe(true)
  })
})

describe('busyDeployments', () => {
  it('collects deployments of queued and running jobs only', () => {
    const busy = busyDeployments([
      { status: 'processing', deployment_ids: [DEP] },
      { status: 'queued', deployment_ids: ['b'] },
      { status: 'completed', deployment_ids: ['c'] },
      { status: 'failed', deployment_ids: null },
    ])
    expect([...busy].sort()).toEqual([DEP, 'b'])
  })

  it('is empty before the job list has loaded', () => {
    expect(busyDeployments(undefined).size).toBe(0)
  })
})
