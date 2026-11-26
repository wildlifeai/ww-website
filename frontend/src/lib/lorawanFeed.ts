/**
 * The Messages tab of the Realtime page (/field): the LoRaWAN messages of the shown deployments
 * that carry decoded values, newest first.
 *
 * - It opens with the newest `FEED_PAGE`; "Load older" reads the next ones before the oldest row,
 *   and the list keeps at most `FEED_CAP`, the newest.
 * - A row is one `lorawan_messages` row with its `lorawan_parsed_messages` row, embedded `!inner`,
 *   so an empty frame (no decoded values) has none. Only `FEED_KINDS` show: check-ins (`report`,
 *   `legacy`) and the camera's answers to a settings downlink (`op_get`, `op_set`, ww-website#516).
 * - The read policies are those of `lorawan_messages` and `lorawan_parsed_messages`, both the
 *   deployment's project (project_viewer and up) and ww_admin, so every role sees the same list.
 * - Supabase Realtime does not resend changes missed while disconnected, so the page reads the
 *   newest page again after a reconnect and on return to the tab; `withNewestPage` merges it.
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { chunk } from './readAllPages'
import { ID_CHUNK } from './realtimeReads'

export const FEED_PAGE = 50
export const FEED_CAP = 500
export const FEED_KINDS = ['report', 'legacy', 'op_get', 'op_set']

export interface FeedRow {
  /** The `lorawan_messages` id. */
  id: string
  deploymentId: string
  receivedAt: string
  /** `lorawan_parsed_messages.kind`, one of `FEED_KINDS`. */
  kind: string
  detections: number | null
  batteryMv: number | null
  /** Percent of the battery byte's scale, today's Cameras table value; used when there is no `batteryMv`. */
  batteryPct: number | null
  sdFreePct: number | null
  /** A settings reply's op number, the value read or set, and its error (1 rejected, 2 no answer). */
  opIndex: number | null
  opValue: number | null
  opError: number | null
}

interface ParsedValues {
  kind: string
  nn_detections: number | null
  battery_mv: number | null
  battery_level: number | null
  sd_free_pct: number | null
  op_index: number | null
  op_value: number | null
  op_error: number | null
}

export interface MessageRow {
  id: string
  deployment_id: string | null
  received_at: string | null
  lorawan_parsed_messages: ParsedValues[]
}

export const FEED_SELECT =
  'id, deployment_id, received_at, lorawan_parsed_messages!inner(kind, nn_detections, battery_mv, battery_level, sd_free_pct, '
  + 'op_index, op_value, op_error)'

export function isFeedKind(kind: string): boolean {
  return FEED_KINDS.includes(kind)
}

/** A message as a feed row, or null when it has no deployment, no time or no decoded row it shows. */
export function toFeedRow(m: MessageRow): FeedRow | null {
  const p = m.lorawan_parsed_messages.find(x => isFeedKind(x.kind))
  if (!p || !m.deployment_id || !m.received_at) return null
  return {
    id: m.id, deploymentId: m.deployment_id, receivedAt: m.received_at, kind: p.kind, detections: p.nn_detections,
    batteryMv: p.battery_mv, batteryPct: p.battery_level, sdFreePct: p.sd_free_pct,
    opIndex: p.op_index, opValue: p.op_value, opError: p.op_error,
  }
}

export function isSettingsReply(r: Pick<FeedRow, 'kind'>): boolean {
  return r.kind === 'op_get' || r.kind === 'op_set'
}

/** `op_error` in words (ww-backend `lorawan_parsed_messages.op_error`). */
const OP_ERRORS: Record<number, string> = {
  1: 'rejected by the AI processor',
  2: 'no answer from the AI processor',
}

/**
 * "Check-in", "Detection +2", or a settings reply: "Setting 16 read: 18", "Setting 16 set to 18",
 * "Setting 16 not changed: rejected by the AI processor".
 */
export function messageText(r: FeedRow): string {
  if (isSettingsReply(r)) {
    const setting = `Setting ${r.opIndex ?? '?'}`
    const read = r.kind === 'op_get'
    if (r.opError != null) {
      const why = OP_ERRORS[r.opError] ?? `error ${r.opError}`
      return `${setting} ${read ? 'not read' : 'not changed'}: ${why}`
    }
    return read ? `${setting} read: ${r.opValue ?? '-'}` : `${setting} set to ${r.opValue ?? '-'}`
  }
  return r.detections && r.detections > 0 ? `Detection +${r.detections}` : 'Check-in'
}

/** Volts, or the percentage older firmware sends; a message here, a camera's latest on the Cameras tab. */
export function batteryText(r: { batteryMv: number | null; batteryPct: number | null }): string {
  if (r.batteryMv != null) return `${(r.batteryMv / 1000).toFixed(1)} V`
  if (r.batteryPct != null) return `${r.batteryPct}%`
  return '-'
}

export function sdFreeText(r: { sdFreePct: number | null }): string {
  return r.sdFreePct == null ? '-' : `${r.sdFreePct}%`
}

/** Newest first, as the database orders them: by time, then id. */
export function byNewest(a: FeedRow, b: FeedRow): number {
  const at = Date.parse(b.receivedAt) - Date.parse(a.receivedAt)
  if (at) return at
  // The same millisecond: the full timestamps (same format and offset) still order, then the id.
  if (a.receivedAt !== b.receivedAt) return a.receivedAt < b.receivedAt ? 1 : -1
  return a.id === b.id ? 0 : a.id < b.id ? 1 : -1
}

/** `rows` and `incoming`, one of each message, newest first, the newest `cap` kept. */
export function mergeFeed(
  rows: FeedRow[], incoming: FeedRow[], cap = FEED_CAP,
): { rows: FeedRow[]; dropped: boolean } {
  const byId = new Map(rows.map(r => [r.id, r]))
  for (const r of incoming) byId.set(r.id, r)
  const all = [...byId.values()].sort(byNewest)
  return { rows: all.slice(0, cap), dropped: all.length > cap }
}

export interface FeedPage {
  rows: FeedRow[]
  /** As many rows as asked for, so there may be more before the oldest. */
  full: boolean
}

export interface FeedState {
  rows: FeedRow[]
  /** There may be messages older than the last row. */
  hasOlder: boolean
  /**
   * The newest message of the first read. A row newer than it arrived while the page was open;
   * null when the first read found none, so every later row did.
   */
  openedAt: string | null
}

/** The newest page merged in: the first read, or a catch-up after a reconnect or a tab return. */
export function withNewestPage(state: FeedState | undefined, page: FeedPage): FeedState {
  if (!state) {
    return { rows: page.rows.slice(0, FEED_CAP), hasOlder: page.full, openedAt: page.rows[0]?.receivedAt ?? null }
  }
  const oldestNew = page.rows.at(-1)
  const newestHeld = state.rows[0]
  // A full page that is all newer than what is held leaves a gap between them: start again from it.
  if (page.full && oldestNew && newestHeld && byNewest(oldestNew, newestHeld) < 0) {
    return { rows: page.rows, hasOlder: true, openedAt: state.openedAt }
  }
  const merged = mergeFeed(state.rows, page.rows)
  return { rows: merged.rows, hasOlder: state.hasOlder || merged.dropped, openedAt: state.openedAt }
}

/** The page read by "Load older" added at the end. */
export function withOlderPage(state: FeedState, page: FeedPage): FeedState {
  const merged = mergeFeed(state.rows, page.rows)
  return { ...state, rows: merged.rows, hasOlder: page.full || merged.dropped }
}

/** A message that arrived while the page is open, added on top. */
export function withLiveRow(state: FeedState, row: FeedRow): FeedState {
  const merged = mergeFeed(state.rows, [row])
  return { ...state, rows: merged.rows, hasOlder: state.hasOlder || merged.dropped }
}

/** The row arrived after the first read, so it stays tinted. */
export function isNew(state: FeedState, row: FeedRow): boolean {
  return state.openedAt === null || Date.parse(row.receivedAt) > Date.parse(state.openedAt)
}

/** How many rows "Load older" asks for: a page, or what is left under the cap. */
export function olderLimit(state: FeedState): number {
  return Math.max(0, Math.min(FEED_PAGE, FEED_CAP - state.rows.length))
}

/**
 * The newest `limit` messages of the deployments, or those before `before`. Each chunk of ids
 * reads its newest `limit`; the newest `limit` of all of them is the page.
 */
export async function fetchFeedPage(
  db: SupabaseClient,
  deploymentIds: string[],
  { before, limit = FEED_PAGE }: { before?: FeedRow; limit?: number } = {},
): Promise<FeedPage> {
  if (deploymentIds.length === 0 || limit <= 0) return { rows: [], full: false }
  const reads = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => {
    let q = db.from('lorawan_messages')
      .select(FEED_SELECT)
      .in('deployment_id', ids)
      .in('lorawan_parsed_messages.kind', FEED_KINDS)
      // DESC puts a message with no time first.
      .not('received_at', 'is', null)
    if (before) {
      const t = `"${before.receivedAt}"`
      q = q.or(`received_at.lt.${t},and(received_at.eq.${t},id.lt.${before.id})`)
    }
    return q.order('received_at', { ascending: false }).order('id', { ascending: false }).limit(limit)
  }))
  const rows: FeedRow[] = []
  for (const { data, error } of reads) {
    if (error) throw new Error(error.message)
    for (const m of (data ?? []) as unknown as MessageRow[]) {
      const r = toFeedRow(m)
      if (r) rows.push(r)
    }
  }
  rows.sort(byNewest)
  return { rows: rows.slice(0, limit), full: rows.length >= limit }
}

/** One message as a feed row, read when its decoded row arrives live; null when it does not show. */
export async function fetchFeedMessage(db: SupabaseClient, messageId: string): Promise<FeedRow | null> {
  const { data, error } = await db.from('lorawan_messages')
    .select(FEED_SELECT)
    .eq('id', messageId)
    .in('lorawan_parsed_messages.kind', FEED_KINDS)
    .limit(1)
  if (error) throw new Error(error.message)
  const m = (data ?? [])[0] as unknown as MessageRow | undefined
  return m ? toFeedRow(m) : null
}

// Formatters at module scope: one made in render hides the component from the React compiler's
// lint (gotchas.md).
const TIME = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
const WEEKDAY_TIME = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
const DATE_TIME = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const WEEK_MS = 6 * 24 * 3_600_000

/** 14:32 today, "Tue 14:32" this week, a date before that; the viewer's zone. */
export function feedTime(iso: string, now = new Date()): string {
  const d = new Date(iso)
  if (d.toDateString() === now.toDateString()) return TIME.format(d)
  if (now.getTime() - d.getTime() < WEEK_MS) return WEEKDAY_TIME.format(d)
  return DATE_TIME.format(d)
}

/** "5m ago", "3h ago", "2d ago"; a dash for no time. */
export function agoLabel(iso: string | null, now = Date.now()): string {
  if (!iso) return '-'
  const mins = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60_000))
  if (mins < 60) return `${mins}m ago`
  const h = Math.round(mins / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}
