import { describe, expect, it } from 'vitest'
import {
  AUTO_RETRY_KEY, THUMBNAIL_GRACE_MS, busyDeployments, dueForAutoRetry, isThumbnailStuck, readAutoRetried,
  rememberAutoRetried, showsBusyDeployment, stuckDeployments,
} from './thumbnailRetry'

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

describe('showsBusyDeployment', () => {
  it('is true only when a deployment in view has a job running', () => {
    expect(showsBusyDeployment([DEP, 'b'], new Set(['b']))).toBe(true)
    expect(showsBusyDeployment([DEP], new Set(['b']))).toBe(false)
    expect(showsBusyDeployment([], new Set(['b']))).toBe(false)
  })
})

describe('stuckDeployments', () => {
  const old = ago(THUMBNAIL_GRACE_MS + 1)
  it('lists each deployment with a card past the grace period once, sorted', () => {
    const cards = [
      { deployment_id: 'b', created_at: old },
      { deployment_id: DEP, created_at: old },
      { deployment_id: 'b', created_at: null },
    ]
    expect(stuckDeployments(cards, NOW, none)).toEqual([DEP, 'b'])
  })

  it('leaves out a deployment whose cards are still inside the grace period', () => {
    expect(stuckDeployments([{ deployment_id: DEP, created_at: ago(60_000) }], NOW, none)).toEqual([])
  })

  it('leaves out a deployment with a job running', () => {
    expect(stuckDeployments([{ deployment_id: DEP, created_at: old }], NOW, new Set([DEP]))).toEqual([])
  })
})

describe('dueForAutoRetry', () => {
  it('retries each stuck deployment once per session', () => {
    expect(dueForAutoRetry([DEP, 'b'], new Set())).toEqual([DEP, 'b'])
    expect(dueForAutoRetry([DEP, 'b'], new Set([DEP]))).toEqual(['b'])
    expect(dueForAutoRetry([DEP], new Set([DEP]))).toEqual([])
  })
})

describe('readAutoRetried and rememberAutoRetried', () => {
  const memory = () => {
    const items = new Map<string, string>()
    return { getItem: (k: string) => items.get(k) ?? null, setItem: (k: string, v: string) => { items.set(k, v) } }
  }

  it('round-trips the retried deployments', () => {
    const store = memory()
    rememberAutoRetried(store, new Set([DEP, 'b']))
    expect([...readAutoRetried(store)].sort()).toEqual([DEP, 'b'])
  })

  it('reads nothing from a missing, corrupt or throwing storage', () => {
    expect(readAutoRetried(null).size).toBe(0)
    expect(readAutoRetried({ getItem: () => '{not json' }).size).toBe(0)
    expect(readAutoRetried({ getItem: () => JSON.stringify({ a: 1 }) }).size).toBe(0)
    expect(readAutoRetried({ getItem: () => { throw new Error('SecurityError') } }).size).toBe(0)
    expect(readAutoRetried({ getItem: k => (k === AUTO_RETRY_KEY ? JSON.stringify([DEP, 3]) : null) })).toEqual(new Set([DEP]))
  })

  it('ignores a storage that refuses the write', () => {
    expect(() => rememberAutoRetried({ setItem: () => { throw new Error('QuotaExceededError') } }, new Set([DEP]))).not.toThrow()
  })
})
