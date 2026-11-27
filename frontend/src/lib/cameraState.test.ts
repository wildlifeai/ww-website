import { describe, expect, it } from 'vitest'
import {
  cameraState, healthReasonText, healthReasonTexts, needsAttention, notRegisteredReason, silenceSpan, silenceText,
  silentAfterHours, toHealthState, NOT_REGISTERED,
  CAMERA_STATE, FRESH_HOURS, STALE_HOURS, type CameraFacts,
} from './cameraState'

const NOW = Date.parse('2026-11-26T12:00:00Z')
const hoursAgo = (h: number) => new Date(NOW - h * 3_600_000).toISOString()

/** A camera deployed 30 days ago with the 4 h ping, its last message an hour ago, health OK. */
const camera = (facts: Partial<CameraFacts> = {}): CameraFacts => ({
  lastSeen: hoursAgo(1), deploymentStart: hoursAgo(30 * 24), deploymentEnd: null, pingMinutes: 240, health: 'ok',
  ...facts,
})
const state = (facts: Partial<CameraFacts>) => cameraState(camera(facts), NOW)

describe('cameraState with a ping period', () => {
  it('goes Silent after 3 periods with no message, 12 h at a 240-minute ping', () => {
    expect(silentAfterHours(240)).toBe(12)
    expect(state({ lastSeen: hoursAgo(11.9) })).toBe('ok')
    expect(state({ lastSeen: hoursAgo(12) })).toBe('ok')
    expect(state({ lastSeen: hoursAgo(12.1) })).toBe('silent')
  })

  it('follows the period: a 60-minute ping is Silent after 3 h', () => {
    expect(state({ pingMinutes: 60, lastSeen: hoursAgo(2.9) })).toBe('ok')
    expect(state({ pingMinutes: 60, lastSeen: hoursAgo(3.1) })).toBe('silent')
  })

  it('is never Stale: a day-old message within 3 daily pings keeps its health', () => {
    expect(state({ pingMinutes: 1440, lastSeen: hoursAgo(30) })).toBe('ok')
  })

  it('with no message, is Awaiting for 3 periods from the start, then Silent, as the alert counts', () => {
    expect(state({ lastSeen: null, deploymentStart: hoursAgo(11) })).toBe('awaiting')
    expect(state({ lastSeen: null, deploymentStart: hoursAgo(13) })).toBe('silent')
    expect(state({ lastSeen: null, deploymentStart: null })).toBe('awaiting')
    expect(state({ lastSeen: null, deploymentStart: hoursAgo(-24) })).toBe('awaiting')
  })
})

describe('cameraState with no period recorded', () => {
  it('ages from its health to Stale after 24 h and Silent after 72 h', () => {
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(1), health: null })).toBe('checking_in')
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(FRESH_HOURS) })).toBe('ok')
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(FRESH_HOURS + 1) })).toBe('stale')
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(STALE_HOURS) })).toBe('stale')
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(STALE_HOURS + 1) })).toBe('silent')
  })

  it('shows a Fault or Warning rather than Stale', () => {
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(30), health: 'fault' })).toBe('fault')
    expect(state({ pingMinutes: null, lastSeen: hoursAgo(30), health: 'warning' })).toBe('warning')
  })

  it('is Awaiting before the first message however long ago it started', () => {
    expect(state({ pingMinutes: null, lastSeen: null, deploymentStart: hoursAgo(500) })).toBe('awaiting')
  })
})

describe('cameraState with LoRaWAN off', () => {
  it('is LoRaWAN off and never Silent or Awaiting', () => {
    expect(silentAfterHours(0)).toBeNull()
    expect(state({ pingMinutes: 0, lastSeen: hoursAgo(1000) })).toBe('off')
    expect(state({ pingMinutes: 0, lastSeen: null })).toBe('off')
    expect(state({ pingMinutes: 0, health: 'fault' })).toBe('off')
  })
})

describe('cameraState health', () => {
  it('shows each health_state, and Checking in without one', () => {
    expect(state({ health: 'fault' })).toBe('fault')
    expect(state({ health: 'warning' })).toBe('warning')
    expect(state({ health: 'ok' })).toBe('ok')
    expect(state({ health: null })).toBe('checking_in')
  })

  it('is Checking in only once a message has come, and never past Stale or Silent', () => {
    expect(state({ health: null, lastSeen: null, deploymentStart: hoursAgo(1) })).toBe('awaiting')
    expect(state({ health: null, pingMinutes: null, lastSeen: hoursAgo(30) })).toBe('stale')
    expect(state({ health: null, lastSeen: hoursAgo(13) })).toBe('silent')
    expect(state({ health: null, pingMinutes: 0 })).toBe('off')
  })

  it('lets Silent override the health state', () => {
    expect(state({ health: 'fault', lastSeen: hoursAgo(13) })).toBe('silent')
    expect(state({ health: 'warning', pingMinutes: null, lastSeen: hoursAgo(73) })).toBe('silent')
  })

  it('reads the view\'s health_state and nothing else as a state', () => {
    expect(['fault', 'warning', 'ok', null, undefined, 'bad'].map(toHealthState))
      .toEqual(['fault', 'warning', 'ok', null, null, null])
  })
})

describe('cameraState once the deployment has ended', () => {
  it('is Finished, never Silent, LoRaWAN off, a health state or Awaiting', () => {
    const ended = hoursAgo(2)
    expect(state({ deploymentEnd: ended })).toBe('finished')
    expect(state({ deploymentEnd: ended, lastSeen: hoursAgo(100) })).toBe('finished')
    expect(state({ deploymentEnd: ended, pingMinutes: 0 })).toBe('finished')
    expect(state({ deploymentEnd: ended, health: 'fault' })).toBe('finished')
    expect(state({ deploymentEnd: ended, lastSeen: null })).toBe('finished')
  })

  it('keeps a deployment whose end is still to come in its live state', () => {
    expect(state({ deploymentEnd: hoursAgo(-3) })).toBe('ok')
  })
})

describe('needsAttention', () => {
  it('lists Silent, Fault and Warning cameras', () => {
    const listed = (Object.keys(CAMERA_STATE) as (keyof typeof CAMERA_STATE)[]).filter(needsAttention)
    expect(listed).toEqual(['silent', 'fault', 'warning'])
  })
})

describe('CAMERA_STATE', () => {
  it('labels every state, Silent and Fault red, Checking in green as OK', () => {
    expect(Object.values(CAMERA_STATE).map(s => s.label)).toEqual([
      'Finished', 'LoRaWAN off', 'Silent', 'Awaiting', 'Fault', 'Warning', 'Stale', 'OK', 'Checking in',
    ])
    expect(CAMERA_STATE.silent.color).toBe('#ef4444')
    expect(CAMERA_STATE.fault.color).toBe('#ef4444')
    expect(CAMERA_STATE.checking_in.color).toBe(CAMERA_STATE.ok.color)
    expect(CAMERA_STATE.off.color).toBe(CAMERA_STATE.awaiting.color)
  })
})

describe('notRegisteredReason', () => {
  it('explains an Awaiting or Silent camera with no DevEUI, and nothing else', () => {
    expect(NOT_REGISTERED).toBe('Not registered for LoRaWAN: contact wildlife.ai')
    expect(notRegisteredReason('awaiting', true)).toBe(NOT_REGISTERED)
    expect(notRegisteredReason('silent', true)).toBe(NOT_REGISTERED)
    for (const state of ['finished', 'off', 'fault', 'warning', 'stale', 'ok', 'checking_in'] as const) {
      expect(notRegisteredReason(state, true)).toBeNull()
    }
    expect(notRegisteredReason('awaiting', false)).toBeNull()
  })
})

describe('silenceText', () => {
  it('says how long the camera has been quiet for it to be Silent', () => {
    expect(silenceText(240)).toBe('No message for over 12 h')
    expect(silenceText(null)).toBe('No message for over 3 days')
    expect(silenceText(1440)).toBe('No message for over 3 days')
    expect(silenceText(720)).toBe('No message for over 36 h')
    expect(silenceText(10)).toBe('No message for over 30 min')
  })

  it('gives the span alone for the check-in choices (#521)', () => {
    expect([240, 360, 480, 720, 1440].map(silenceSpan)).toEqual(['12 h', '18 h', '24 h', '36 h', '3 days'])
  })
})

describe('healthReasonText', () => {
  it('names each self-test bit, and a bit with no name by its number', () => {
    expect(healthReasonText('self_test_bit_0')).toBe('Battery low')
    expect(healthReasonText('self_test_bit_1')).toBe('AI processor not responding')
    expect(healthReasonText('self_test_bit_8')).toBe('Main camera failed')
    expect(healthReasonText('self_test_bit_9')).toBe('HM0360 camera failed')
    expect(healthReasonText('self_test_bit_11')).toBe('SD card missing')
    expect(healthReasonText('self_test_bit_12')).toBe('Settings file damaged, photo counters restarted')
    expect(healthReasonText('self_test_bit_7')).toBe('Self-test bit 7')
  })

  it('names an unexpected restart by its reason', () => {
    expect(healthReasonText('unexpected_restart_6')).toBe('Restarted by its watchdog')
    expect(healthReasonText('unexpected_restart_5')).toBe('Restarted after a hard fault')
    expect(healthReasonText('unexpected_restart_42')).toBe('Restarted unexpectedly (reason 42)')
    // A firmware update reports 10 and is no warning (ww-backend#398), so it has no words of its own.
    expect(healthReasonText('unexpected_restart_10')).toBe('Restarted unexpectedly (reason 10)')
  })

  it('names the SD card and AI processor reasons, and passes an unknown code through', () => {
    expect(healthReasonText('sd_low')).toBe('SD card nearly full')
    expect(healthReasonText('ai_failures_rose')).toBe('AI processor did not respond')
    expect(healthReasonText('something_new')).toBe('something_new')
  })

  it('keeps the view\'s order and says each thing once', () => {
    expect(healthReasonTexts(['self_test_bit_9', 'self_test_bit_3', 'unexpected_restart_6', 'sd_low']))
      .toEqual(['HM0360 camera failed', 'Restarted by its watchdog', 'SD card nearly full'])
    expect(healthReasonTexts(null)).toEqual([])
  })
})
