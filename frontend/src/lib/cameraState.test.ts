import { describe, expect, it } from 'vitest'
import { cameraState, CAMERA_STATE, FRESH_HOURS, STALE_HOURS } from './cameraState'

const NOW = Date.parse('2026-11-26T12:00:00Z')
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString()

describe('cameraState', () => {
  it('is Awaiting before the first message', () => {
    expect(cameraState({ lastSeen: null, deploymentEnd: null }, NOW)).toBe('awaiting')
  })

  it('ages from Fresh to Stale to Silent', () => {
    expect(cameraState({ lastSeen: hoursAgo(1), deploymentEnd: null }, NOW)).toBe('fresh')
    expect(cameraState({ lastSeen: hoursAgo(FRESH_HOURS), deploymentEnd: null }, NOW)).toBe('fresh')
    expect(cameraState({ lastSeen: hoursAgo(FRESH_HOURS + 1), deploymentEnd: null }, NOW)).toBe('stale')
    expect(cameraState({ lastSeen: hoursAgo(STALE_HOURS), deploymentEnd: null }, NOW)).toBe('stale')
    expect(cameraState({ lastSeen: hoursAgo(STALE_HOURS + 1), deploymentEnd: null }, NOW)).toBe('silent')
  })

  it('is Finished once the deployment has ended, never Silent, Stale or Awaiting', () => {
    const ended = hoursAgo(2)
    expect(cameraState({ lastSeen: hoursAgo(1), deploymentEnd: ended }, NOW)).toBe('finished')
    expect(cameraState({ lastSeen: hoursAgo(30), deploymentEnd: ended }, NOW)).toBe('finished')
    expect(cameraState({ lastSeen: hoursAgo(100), deploymentEnd: ended }, NOW)).toBe('finished')
    expect(cameraState({ lastSeen: null, deploymentEnd: ended }, NOW)).toBe('finished')
  })

  it('keeps a deployment whose end is still to come in its live state', () => {
    expect(cameraState({ lastSeen: hoursAgo(1), deploymentEnd: hoursAgo(-3) }, NOW)).toBe('fresh')
  })

  it('labels every state', () => {
    expect(Object.values(CAMERA_STATE).map(s => s.label)).toEqual(['Fresh', 'Stale', 'Silent', 'Awaiting', 'Finished'])
  })
})
