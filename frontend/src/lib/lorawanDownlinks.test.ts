import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  canRequest, CHECK_IN_OP, DOWNLINK_COLUMNS, fetchCheckInReply, fetchLatestDownlink, isInFlight, knownPeriod,
  requestDownlink, requestText, type Downlink,
} from './lorawanDownlinks'

const time = (iso: string) => iso.slice(11, 16)

function downlink(over: Partial<Downlink>): Downlink {
  return {
    id: 'r-1', deployment_id: 'd-1', kind: 'op_set', op_index: CHECK_IN_OP, op_value: 360, status: 'sent', error: null,
    requested_at: '2026-11-27T01:00:00Z', answered_at: null, reply_message_id: null, ...over,
  }
}

/** A query builder that records its calls and resolves to `answer`. */
function fakeDb(answer: { data: unknown; error: unknown }) {
  const calls: unknown[][] = []
  const builder: Record<string, unknown> = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => Promise.resolve(answer).then(resolve, reject),
  }
  for (const m of ['select', 'eq', 'in', 'is', 'not', 'order', 'limit']) {
    builder[m] = (...args: unknown[]) => { calls.push([m, ...args]); return builder }
  }
  const from = vi.fn(() => builder)
  return { db: { from } as unknown as SupabaseClient, calls, from }
}

/** A functions client whose invoke answers `result`. */
function fakeFunctions(result: { data: unknown; error: unknown }) {
  const invoke = vi.fn(async () => result)
  return { db: { functions: { invoke } } as unknown as Pick<SupabaseClient, 'functions'>, invoke }
}

/** functions-js FunctionsHttpError: the response is on `context`. */
const httpError = (status: number, body: unknown) => ({
  message: 'Edge Function returned a non-2xx status code',
  context: new Response(JSON.stringify(body), { status }),
})

describe('one request at a time', () => {
  it('is in flight while queued or sent, and blocks the next', () => {
    for (const status of ['queued', 'sent'] as const) {
      expect(isInFlight({ status })).toBe(true)
      expect(canRequest({ status })).toBe(false)
    }
    for (const status of ['answered', 'failed', 'expired'] as const) {
      expect(isInFlight({ status })).toBe(false)
      expect(canRequest({ status })).toBe(true)
    }
    expect(canRequest(null)).toBe(true)
  })
})

describe('requestText', () => {
  it('waits for the next check-in, at the deployment period', () => {
    expect(requestText(downlink({ status: 'queued' }), 240, null, time))
      .toBe("Waiting for the camera's next check-in (every 4 hours).")
    expect(requestText(downlink({ status: 'sent' }), null, null, time)).toBe("Waiting for the camera's next check-in.")
  })

  it('says the value read or set, with the time', () => {
    const at = '2026-11-27T04:32:00Z'
    expect(requestText(downlink({ status: 'answered', answered_at: at }), 240, null, time))
      .toBe('Check-in period set to 6 hours at 04:32.')
    expect(requestText(downlink({ kind: 'op_get', op_value: null, status: 'answered', answered_at: at }), 240, 240, time))
      .toBe('Check-in period read: 4 hours at 04:32.')
    expect(requestText(downlink({ kind: 'op_get', op_value: null, status: 'answered', answered_at: at }), 240, 5, time))
      .toBe('Check-in period read: 5 minutes at 04:32.')
  })

  it("words the camera's error as the Messages tab does", () => {
    const at = '2026-11-27T04:32:00Z'
    expect(requestText(downlink({ status: 'answered', answered_at: at, error: 'The camera refused the setting or value' }), 240, null, time))
      .toBe('Not changed: rejected by the AI processor at 04:32.')
    expect(requestText(downlink({ kind: 'op_get', status: 'answered', answered_at: at, error: 'The AI processor did not answer' }), 240, null, time))
      .toBe('Not read: no answer from the AI processor at 04:32.')
  })

  it('says why a request was not sent', () => {
    const failed = (error: string | null) => requestText(downlink({ status: 'failed', error }), 240, null, time)
    expect(failed('No network connection for this camera: no TTN API key is stored for its application'))
      .toBe('Not sent: no network connection for this camera.')
    expect(failed('TTN answered 403: {"code":7}')).toBe('Not sent: The Things Network refused it or could not be reached.')
    expect(failed('TTN could not be reached: timeout')).toBe('Not sent: The Things Network refused it or could not be reached.')
    expect(failed('Not sent: the request stopped before it reached TTN'))
      .toBe('Not sent: it stopped before it reached The Things Network.')
    expect(failed(null)).toBe('Not sent.')
  })

  it('expires after 2 periods, or 24 hours with none recorded', () => {
    expect(requestText(downlink({ status: 'expired' }), 240, null, time)).toBe('No answer from the camera within 8 hours.')
    expect(requestText(downlink({ status: 'expired' }), null, null, time)).toBe('No answer from the camera within 24 hours.')
  })
})

describe('knownPeriod', () => {
  it("prefers the camera's newest reply, then the deployment's period", () => {
    const reply = { messageId: 'm-1', receivedAt: '2026-11-27T04:32:00Z', kind: 'op_get' as const, minutes: 5 }
    expect(knownPeriod(reply, 240)).toEqual({ minutes: 5, from: 'camera', at: reply.receivedAt })
    expect(knownPeriod(null, 240)).toEqual({ minutes: 240, from: 'deployment' })
    expect(knownPeriod(null, null)).toBeNull()
  })
})

describe('reads', () => {
  it("reads the deployment's newest request", async () => {
    const row = downlink({})
    const { db, calls, from } = fakeDb({ data: [row], error: null })
    await expect(fetchLatestDownlink(db, 'd-1')).resolves.toEqual(row)
    expect(from).toHaveBeenCalledWith('lorawan_downlinks')
    expect(calls).toEqual([
      ['select', DOWNLINK_COLUMNS], ['eq', 'deployment_id', 'd-1'], ['order', 'requested_at', { ascending: false }], ['limit', 1],
    ])
    await expect(fetchLatestDownlink(fakeDb({ data: [], error: null }).db, 'd-1')).resolves.toBeNull()
  })

  it('reads the newest op 32 reply without an error', async () => {
    const { db, calls, from } = fakeDb({
      data: [{ id: 'm-1', received_at: '2026-11-27T04:32:00Z', lorawan_parsed_messages: [{ kind: 'op_set', op_value: 360 }] }],
      error: null,
    })
    await expect(fetchCheckInReply(db, 'd-1'))
      .resolves.toEqual({ messageId: 'm-1', receivedAt: '2026-11-27T04:32:00Z', kind: 'op_set', minutes: 360 })
    expect(from).toHaveBeenCalledWith('lorawan_messages')
    expect(calls).toContainEqual(['eq', 'lorawan_parsed_messages.op_index', 32])
    expect(calls).toContainEqual(['is', 'lorawan_parsed_messages.op_error', null])
    expect(calls).toContainEqual(['in', 'lorawan_parsed_messages.kind', ['op_get', 'op_set']])
    await expect(fetchCheckInReply(fakeDb({ data: [], error: null }).db, 'd-1')).resolves.toBeNull()
  })

  it('throws what the database said', async () => {
    await expect(fetchLatestDownlink(fakeDb({ data: null, error: { message: 'boom' } }).db, 'd-1')).rejects.toThrow('boom')
  })
})

describe('requestDownlink', () => {
  it('sends a read without a value and a set with one, for op 32', async () => {
    const { db, invoke } = fakeFunctions({ data: { ok: true, id: 'r-1', status: 'sent' }, error: null })
    await expect(requestDownlink(db, 'd-1', { kind: 'op_get' })).resolves.toEqual({ ok: true, id: 'r-1' })
    expect(invoke).toHaveBeenLastCalledWith('lorawan-downlink', { body: { deployment_id: 'd-1', kind: 'op_get', op_index: 32 } })
    await requestDownlink(db, 'd-1', { kind: 'op_set', value: 360 })
    expect(invoke).toHaveBeenLastCalledWith('lorawan-downlink',
      { body: { deployment_id: 'd-1', kind: 'op_set', op_index: 32, op_value: 360 } })
  })

  it.each([
    [409, 'not_open', "This camera's deployment has ended."],
    [409, 'no_ttn_message', 'The camera has sent nothing through The Things Network yet, so there is nowhere to send this.'],
    [409, 'in_flight', 'The camera already has a request waiting for its next check-in.'],
    [429, 'rate_limited', 'This camera has had 3 requests in the last 24 hours, the most allowed. Try again later.'],
    [503, 'no_network', 'Not sent: no network connection for this camera.'],
    [502, 'ttn_failed', 'Not sent: The Things Network refused it or could not be reached.'],
  ])('words a %i %s refusal', async (status, code, text) => {
    const { db } = fakeFunctions({ data: null, error: httpError(status, { ok: false, code, message: 'backend words' }) })
    await expect(requestDownlink(db, 'd-1', { kind: 'op_get' })).resolves.toEqual({ ok: false, code, text })
  })

  it("falls back to the function's message, then to plain words", async () => {
    const unknown = fakeFunctions({ data: null, error: httpError(400, { ok: false, code: 'new_code', message: 'New rule.' }) })
    await expect(requestDownlink(unknown.db, 'd-1', { kind: 'op_get' })).resolves.toEqual({ ok: false, code: 'new_code', text: 'New rule.' })
    const gateway = fakeFunctions({ data: null, error: httpError(401, { code: 401, message: 'Invalid JWT' }) })
    await expect(requestDownlink(gateway.db, 'd-1', { kind: 'op_get' })).resolves.toEqual({ ok: false, code: 'error', text: 'Invalid JWT' })
    const offline = fakeFunctions({ data: null, error: { message: 'Failed to send a request to the Edge Function' } })
    await expect(requestDownlink(offline.db, 'd-1', { kind: 'op_get' }))
      .resolves.toEqual({ ok: false, code: 'unreachable', text: 'Could not reach the server. Try again.' })
  })
})
