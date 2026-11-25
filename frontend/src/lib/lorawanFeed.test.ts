import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  batteryText, byNewest, fetchFeedMessage, fetchFeedPage, isFeedKind, isNew, mergeFeed, messageText,
  olderLimit, sdFreeText, toFeedRow, withLiveRow, withNewestPage, withOlderPage,
  FEED_CAP, FEED_PAGE, FEED_SELECT, type FeedRow, type FeedState, type MessageRow,
} from './lorawanFeed'
import { ID_CHUNK } from './realtimeReads'

type Call = [string, ...unknown[]]
type Answer = { data: unknown[] | null; error: { message: string } | null }

/** A client that records each query's calls; awaiting the chain answers it. */
function fakeDb(answer: (calls: Call[], n: number) => Answer) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      const n = queries.length
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'not', 'or', 'order', 'limit']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.then = (resolve: (v: Answer) => void) => resolve(answer(calls, n))
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

/** A message `min` minutes after 10:00 on 26 Nov 2026, with one decoded row. */
function message(id: string, min: number, parsed: Partial<MessageRow['lorawan_parsed_messages'][number]> = {}): MessageRow {
  return {
    id, deployment_id: 'd1', received_at: at(min),
    lorawan_parsed_messages: [{
      kind: 'report', nn_detections: null, battery_mv: null, battery_level: null, sd_free_pct: null, ...parsed,
    }],
  }
}
const at = (min: number) => new Date(Date.parse('2026-11-26T10:00:00Z') + min * 60_000).toISOString()
const row = (id: string, min: number): FeedRow => toFeedRow(message(id, min))!
/** Rows newest first: ids r<n> at minute n, for n from `newest` down to `oldest`. */
const rows = (newest: number, oldest: number) =>
  Array.from({ length: newest - oldest + 1 }, (_, i) => row(`r${newest - i}`, newest - i))
const state = (rs: FeedRow[], extra: Partial<FeedState> = {}): FeedState =>
  ({ rows: rs, hasOlder: false, openedAt: rs[0]?.receivedAt ?? null, ...extra })

describe('toFeedRow and its texts', () => {
  it('is a Check-in without a detection, and Detection +N with one', () => {
    expect(messageText(toFeedRow(message('m', 0))!)).toBe('Check-in')
    expect(messageText(toFeedRow(message('m', 0, { nn_detections: 0 }))!)).toBe('Check-in')
    expect(messageText(toFeedRow(message('m', 0, { nn_detections: 3 }))!)).toBe('Detection +3')
  })

  it('shows volts from battery_mv, else the percentage, else a dash', () => {
    expect(batteryText(toFeedRow(message('m', 0, { battery_mv: 5432, battery_level: 72 }))!)).toBe('5.4 V')
    expect(batteryText(toFeedRow(message('m', 0, { kind: 'legacy', battery_level: 87 }))!)).toBe('87%')
    expect(batteryText(toFeedRow(message('m', 0))!)).toBe('-')
  })

  it('shows SD free, and a dash when the camera reports it unknown', () => {
    expect(sdFreeText(toFeedRow(message('m', 0, { sd_free_pct: 41 }))!)).toBe('41%')
    expect(sdFreeText(toFeedRow(message('m', 0, { sd_free_pct: 0 }))!)).toBe('0%')
    expect(sdFreeText(toFeedRow(message('m', 0, { sd_free_pct: null }))!)).toBe('-')
  })

  it('maps the message and its decoded values', () => {
    expect(toFeedRow(message('m1', 0, { nn_detections: 2, battery_mv: 5600, battery_level: 76, sd_free_pct: 41 }))).toEqual({
      id: 'm1', deploymentId: 'd1', receivedAt: at(0), detections: 2, batteryMv: 5600, batteryPct: 76, sdFreePct: 41,
    })
  })

  it('leaves settings replies out', () => {
    expect(isFeedKind('report')).toBe(true)
    expect(isFeedKind('legacy')).toBe(true)
    expect(isFeedKind('op_get')).toBe(false)
    expect(isFeedKind('op_set')).toBe(false)
    expect(toFeedRow(message('m', 0, { kind: 'op_get' }))).toBeNull()
    expect(toFeedRow(message('m', 0, { kind: 'op_set' }))).toBeNull()
  })

  it('needs a deployment and a time', () => {
    expect(toFeedRow({ ...message('m', 0), deployment_id: null })).toBeNull()
    expect(toFeedRow({ ...message('m', 0), received_at: null })).toBeNull()
  })
})

describe('byNewest', () => {
  it('orders by time, then by the full timestamp within a millisecond, then by id', () => {
    const r = (id: string, receivedAt: string) => ({ ...row(id, 0), receivedAt })
    const sorted = [
      r('a', '2026-11-26T10:00:00.1234+00:00'),
      r('b', '2026-11-26T10:00:01+00:00'),
      r('c', '2026-11-26T10:00:00.1239+00:00'),
      r('d', '2026-11-26T10:00:00.1239+00:00'),
    ].sort(byNewest)
    expect(sorted.map(x => x.id)).toEqual(['b', 'd', 'c', 'a'])
  })
})

describe('mergeFeed', () => {
  it('keeps one row per message, newest first', () => {
    const updated = { ...row('r2', 2), detections: 4 }
    const merged = mergeFeed([row('r3', 3), row('r2', 2)], [row('r5', 5), updated, row('r1', 1)])
    expect(merged.rows.map(r => r.id)).toEqual(['r5', 'r3', 'r2', 'r1'])
    expect(merged.rows[2].detections).toBe(4)
    expect(merged.dropped).toBe(false)
  })

  it(`keeps the newest ${FEED_CAP}`, () => {
    const merged = mergeFeed(rows(FEED_CAP, 1), [row('new', FEED_CAP + 1)])
    expect(merged.rows).toHaveLength(FEED_CAP)
    expect(merged.rows[0].id).toBe('new')
    expect(merged.rows.at(-1)?.id).toBe('r2')
    expect(merged.dropped).toBe(true)
  })
})

describe('withNewestPage', () => {
  it('opens with the first read, which is not new', () => {
    const s = withNewestPage(undefined, { rows: rows(60, 11), full: true })
    expect(s.rows).toHaveLength(50)
    expect(s.hasOlder).toBe(true)
    expect(s.openedAt).toBe(at(60))
    expect(s.rows.some(r => isNew(s, r))).toBe(false)
  })

  it('treats every row after an empty first read as new', () => {
    const s = withNewestPage(undefined, { rows: [], full: false })
    expect(s).toEqual({ rows: [], hasOlder: false, openedAt: null })
    expect(isNew(withLiveRow(s, row('r1', 1)), row('r1', 1))).toBe(true)
  })

  it('merges a catch-up that overlaps, keeping the older rows loaded', () => {
    const before = state(rows(20, 1), { hasOlder: true })
    const s = withNewestPage(before, { rows: rows(25, 10), full: false })
    expect(s.rows.map(r => r.id)).toEqual(rows(25, 1).map(r => r.id))
    expect(s.hasOlder).toBe(true)
    expect(s.openedAt).toBe(at(20))
    expect(s.rows.filter(r => isNew(s, r)).map(r => r.id)).toEqual(['r25', 'r24', 'r23', 'r22', 'r21'])
  })

  it('starts again from a full catch-up page that leaves a gap', () => {
    const before = state(rows(20, 1))
    const s = withNewestPage(before, { rows: rows(120, 71), full: true })
    expect(s.rows.map(r => r.id)).toEqual(rows(120, 71).map(r => r.id))
    expect(s.hasOlder).toBe(true)
    expect(s.openedAt).toBe(at(20))
  })
})

describe('withLiveRow', () => {
  it('adds a live message on top, tinted, and leaves the first read untinted', () => {
    const s = withLiveRow(state(rows(3, 1)), row('live', 10))
    expect(s.rows.map(r => r.id)).toEqual(['live', 'r3', 'r2', 'r1'])
    expect(s.rows.filter(r => isNew(s, r)).map(r => r.id)).toEqual(['live'])
  })

  it('ignores a message it already holds', () => {
    const s = withLiveRow(state(rows(3, 1)), row('r3', 3))
    expect(s.rows).toHaveLength(3)
  })

  it(`drops the oldest at ${FEED_CAP} rows, so older ones can be read again`, () => {
    const s = withLiveRow(state(rows(FEED_CAP, 1)), row('live', FEED_CAP + 1))
    expect(s.rows).toHaveLength(FEED_CAP)
    expect(s.hasOlder).toBe(true)
  })
})

describe('withOlderPage and olderLimit', () => {
  it('adds the page at the end', () => {
    const s = withOlderPage(state(rows(60, 11), { hasOlder: true }), { rows: rows(10, 1), full: false })
    expect(s.rows.map(r => r.id)).toEqual(rows(60, 1).map(r => r.id))
    expect(s.hasOlder).toBe(false)
  })

  it(`asks for a page, or what is left under ${FEED_CAP}`, () => {
    expect(olderLimit(state(rows(50, 1)))).toBe(FEED_PAGE)
    expect(olderLimit(state(rows(FEED_CAP - 20, 1)))).toBe(20)
    expect(olderLimit(state(rows(FEED_CAP, 1)))).toBe(0)
  })
})

describe('fetchFeedPage', () => {
  it('reads the newest messages with decoded check-ins and detections, newest first', async () => {
    const { db, queries } = fakeDb(() => ({ data: [message('m2', 2), message('m1', 1)], error: null }))
    const page = await fetchFeedPage(db, ['d1', 'd2'])
    expect(page).toEqual({ rows: [row('m2', 2), row('m1', 1)], full: false })
    expect(queries).toEqual([[
      ['from', 'lorawan_messages'],
      ['select', FEED_SELECT],
      ['in', 'deployment_id', ['d1', 'd2']],
      ['in', 'lorawan_parsed_messages.kind', ['report', 'legacy']],
      ['not', 'received_at', 'is', null],
      ['order', 'received_at', { ascending: false }],
      ['order', 'id', { ascending: false }],
      ['limit', FEED_PAGE],
    ]])
  })

  it('reads before a row, the same time broken by id', async () => {
    const before = row('m5', 5)
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchFeedPage(db, ['d1'], { before, limit: 20 })).toEqual({ rows: [], full: false })
    const t = `"${at(5)}"`
    expect(queries[0]).toContainEqual(['or', `received_at.lt.${t},and(received_at.eq.${t},id.lt.m5)`])
    expect(queries[0]).toContainEqual(['limit', 20])
  })

  it('takes the newest of every chunk of deployments', async () => {
    const ids = Array.from({ length: ID_CHUNK + 1 }, (_, i) => `d${i}`)
    const { db, queries } = fakeDb((_, n) => ({
      data: n === 0 ? [message('a3', 3), message('a1', 1)] : [message('b2', 2)], error: null,
    }))
    const page = await fetchFeedPage(db, ids, { limit: 2 })
    expect(queries).toHaveLength(2)
    expect(page.rows.map(r => r.id)).toEqual(['a3', 'b2'])
    expect(page.full).toBe(true)
  })

  it('sends no request for no deployments', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchFeedPage(db, [])).toEqual({ rows: [], full: false })
    expect(queries).toHaveLength(0)
  })

  it('throws a refused read', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'permission denied' } }))
    await expect(fetchFeedPage(db, ['d1'])).rejects.toThrow('permission denied')
  })
})

describe('fetchFeedMessage', () => {
  it('reads one message as a feed row', async () => {
    const { db, queries } = fakeDb(() => ({ data: [message('m1', 1, { nn_detections: 2 })], error: null }))
    expect(await fetchFeedMessage(db, 'm1')).toEqual({ ...row('m1', 1), detections: 2 })
    expect(queries[0]).toContainEqual(['eq', 'id', 'm1'])
    expect(queries[0]).toContainEqual(['in', 'lorawan_parsed_messages.kind', ['report', 'legacy']])
  })

  it('is null when the message does not show', async () => {
    const { db } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchFeedMessage(db, 'm1')).toBeNull()
  })
})
