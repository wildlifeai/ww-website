import { describe, expect, it, vi } from 'vitest'
import { FEATURE_PROBES, isFeatureDisabled, probeFeature } from './featureFlags'

const disabled = Object.assign(new Error('off'), { code: 'FEATURE_DISABLED' })

describe('isFeatureDisabled', () => {
  it('is true only for FEATURE_DISABLED', () => {
    expect(isFeatureDisabled(disabled)).toBe(true)
    expect(isFeatureDisabled(Object.assign(new Error('x'), { code: 'HTTP_403' }))).toBe(false)
    expect(isFeatureDisabled(new Error('x'))).toBe(false)
    expect(isFeatureDisabled(null)).toBe(false)
    expect(isFeatureDisabled(undefined)).toBe(false)
  })
})

describe('probeFeature', () => {
  it('is true when the route answers', async () => {
    await expect(probeFeature(vi.fn().mockResolvedValue({ data: {} }))).resolves.toBe(true)
  })

  it('is false when the route says FEATURE_DISABLED', async () => {
    await expect(probeFeature(vi.fn().mockRejectedValue(disabled))).resolves.toBe(false)
  })

  it('rethrows any other failure, which says nothing about the flag', async () => {
    const denied = Object.assign(new Error('no access'), { code: 'HTTP_403' })
    await expect(probeFeature(vi.fn().mockRejectedValue(denied))).rejects.toBe(denied)
  })
})

describe('FEATURE_PROBES', () => {
  it('asks a cheap route of the right kind for each feature', () => {
    expect(FEATURE_PROBES.intelligence('p1')).toBe('/api/intelligence/alerts/p1')
    expect(FEATURE_PROBES.brain('d1')).toBe('/api/brain/embedding-runs/d1')
    expect(FEATURE_PROBES.activeLearning('d1')).toBe('/api/brain/review-queue/d1?limit=1')
  })
})
