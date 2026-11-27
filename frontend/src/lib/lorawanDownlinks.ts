/**
 * Reading and setting a camera's check-in period over LoRaWAN from the Realtime page (#521).
 *
 * ww-backend owns the whole contract (#403, merged as #405; LORAWAN_INGEST.md, "Downlinks"):
 *
 * - The website calls the `lorawan-downlink` function with the user's session:
 *   `{ deployment_id, kind: 'op_get' | 'op_set', op_index, op_value? }`. The function does every
 *   check (`request_lorawan_downlink`) and answers `{ ok, code?, message?, id?, status? }`. Only
 *   op 32, the check-in period, may be set, to 60 to 1440 minutes.
 * - Each request is a `lorawan_downlinks` row, read by the deployment's project and written only
 *   by the service role. `status` goes queued (not yet at TTN), sent (in TTN's queue, taken
 *   after the camera's next uplink), then answered, failed (never reached the camera; a request
 *   still queued after 5 minutes fails) or expired (no reply within 2 ping periods, 24 hours with
 *   none recorded).
 * - The camera takes one request at a time (a unique index on its in-flight rows), and at most 3
 *   in 24 hours, inside The Things Network's 10 downlinks a day.
 * - The camera's reply is a `lorawan_parsed_messages` row (kind op_get or op_set, op_value,
 *   op_error), which also answers the request; a set of op 32 also becomes the deployment's
 *   `lorawan_ping_minutes`.
 *
 * The page hides the controls from anyone the function would refuse as not a project admin; every
 * other refusal is worded here.
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { formatPing } from './lorawanSettings'

/** op 32 CHECK_IN_PERIOD, minutes between check-ins. */
export const CHECK_IN_OP = 32

export type DownlinkKind = 'op_get' | 'op_set'
export type DownlinkStatus = 'queued' | 'sent' | 'answered' | 'failed' | 'expired'

export interface Downlink {
  id: string
  deployment_id: string
  kind: DownlinkKind
  op_index: number
  op_value: number | null
  status: DownlinkStatus
  error: string | null
  requested_at: string
  answered_at: string | null
  reply_message_id: string | null
}

export const DOWNLINK_COLUMNS =
  'id, deployment_id, kind, op_index, op_value, status, error, requested_at, answered_at, reply_message_id'

/** The camera's newest answer about its check-in period that carried a value. */
export interface CheckInReply {
  messageId: string
  receivedAt: string
  kind: DownlinkKind
  minutes: number
}

/** The camera's newest request, or null for none. */
export async function fetchLatestDownlink(db: SupabaseClient, deploymentId: string): Promise<Downlink | null> {
  const { data, error } = await db.from('lorawan_downlinks')
    .select(DOWNLINK_COLUMNS)
    .eq('deployment_id', deploymentId)
    .order('requested_at', { ascending: false })
    .limit(1)
  if (error) throw new Error(error.message)
  return ((data ?? [])[0] as Downlink | undefined) ?? null
}

interface ReplyRow {
  id: string
  received_at: string
  lorawan_parsed_messages: { kind: DownlinkKind; op_value: number | null }[]
}

/**
 * The newest op_get or op_set reply about op 32 without an error, whoever sent the request (the
 * page, the app or the TTN console), or null for none.
 */
export async function fetchCheckInReply(db: SupabaseClient, deploymentId: string): Promise<CheckInReply | null> {
  const { data, error } = await db.from('lorawan_messages')
    .select('id, received_at, lorawan_parsed_messages!inner(kind, op_value)')
    .eq('deployment_id', deploymentId)
    .in('lorawan_parsed_messages.kind', ['op_get', 'op_set'])
    .eq('lorawan_parsed_messages.op_index', CHECK_IN_OP)
    .is('lorawan_parsed_messages.op_error', null)
    .not('received_at', 'is', null)
    .order('received_at', { ascending: false })
    .limit(1)
  if (error) throw new Error(error.message)
  const m = (data ?? [])[0] as unknown as ReplyRow | undefined
  const p = m?.lorawan_parsed_messages[0]
  if (!m || !p || p.op_value == null) return null
  return { messageId: m.id, receivedAt: m.received_at, kind: p.kind, minutes: p.op_value }
}

export interface CheckInState {
  request: Downlink | null
  reply: CheckInReply | null
}

/** Both reads the panel needs. */
export async function fetchCheckInState(db: SupabaseClient, deploymentId: string): Promise<CheckInState> {
  const [request, reply] = await Promise.all([fetchLatestDownlink(db, deploymentId), fetchCheckInReply(db, deploymentId)])
  return { request, reply }
}

export function isInFlight(d: Pick<Downlink, 'status'> | null | undefined): boolean {
  return d?.status === 'queued' || d?.status === 'sent'
}

/** One request at a time: none while the camera's newest is still queued or sent. */
export function canRequest(latest: Pick<Downlink, 'status'> | null | undefined): boolean {
  return !isInFlight(latest)
}

/**
 * The period the camera last reported, else the one it was deployed with (or last set to, which
 * ww-backend copies to the deployment). Null when neither is known.
 */
export function knownPeriod(
  reply: CheckInReply | null, deploymentMinutes: number | null,
): { minutes: number; from: 'camera' | 'deployment'; at?: string } | null {
  if (reply) return { minutes: reply.minutes, from: 'camera', at: reply.receivedAt }
  if (deploymentMinutes != null) return { minutes: deploymentMinutes, from: 'deployment' }
  return null
}

// The camera's error as ww-backend's match_lorawan_downlink_reply words it on the request (op_error
// 1 and 2), in the Messages tab's words (lorawanFeed.ts).
const CAMERA_ERRORS: Record<string, string> = {
  'The camera refused the setting or value': 'rejected by the AI processor',
  'The AI processor did not answer': 'no answer from the AI processor',
}

/** Why a request failed, from its row's `error`. */
function failedText(error: string | null): string {
  if (!error) return 'Not sent.'
  if (error.startsWith('No network connection')) return 'Not sent: no network connection for this camera.'
  if (error.startsWith('TTN answered') || error.startsWith('TTN could not be reached')) {
    return 'Not sent: The Things Network refused it or could not be reached.'
  }
  if (error.includes('stopped before it reached TTN')) return 'Not sent: it stopped before it reached The Things Network.'
  return `Not sent: ${error}`
}

/**
 * Where the camera's newest request is, in words. `pingMinutes` is the deployment's check-in
 * period, null when not recorded; `replyMinutes` the value of the reply that answered it.
 */
export function requestText(
  d: Downlink, pingMinutes: number | null, replyMinutes: number | null, time: (iso: string) => string,
): string {
  const read = d.kind === 'op_get'
  const setting = d.op_index === CHECK_IN_OP ? 'Check-in period' : `Setting ${d.op_index}`
  const value = (v: number | null) => (v == null ? '-' : d.op_index === CHECK_IN_OP ? formatPing(v) : String(v))
  switch (d.status) {
    case 'queued':
    case 'sent':
      return pingMinutes
        ? `Waiting for the camera's next check-in (every ${formatPing(pingMinutes)}).`
        : "Waiting for the camera's next check-in."
    case 'answered': {
      const at = d.answered_at ? ` at ${time(d.answered_at)}` : ''
      if (d.error) return `${read ? 'Not read' : 'Not changed'}: ${CAMERA_ERRORS[d.error] ?? d.error}${at}.`
      return read ? `${setting} read: ${value(replyMinutes)}${at}.` : `${setting} set to ${value(d.op_value)}${at}.`
    }
    case 'failed':
      return failedText(d.error)
    case 'expired':
      return `No answer from the camera within ${pingMinutes ? formatPing(2 * pingMinutes) : '24 hours'}.`
  }
}

/** The function's refusals and failures, in words; the message it sent for anything else. */
const REFUSALS: Record<string, string> = {
  not_open: "This camera's deployment has ended.",
  no_ttn_message: 'The camera has sent nothing through The Things Network yet, so there is nowhere to send this.',
  in_flight: 'The camera already has a request waiting for its next check-in.',
  rate_limited: 'This camera has had 3 requests in the last 24 hours, the most allowed. Try again later.',
  no_network: 'Not sent: no network connection for this camera.',
  ttn_failed: 'Not sent: The Things Network refused it or could not be reached.',
  not_allowed: 'Only a project admin can send a camera a request.',
  not_found: 'This deployment no longer exists.',
  setting_not_allowed: 'That setting or value cannot be sent.',
  bad_request: 'That setting or value cannot be sent.',
  unauthorized: 'Sign in again, then try again.',
}

export type DownlinkResult = { ok: true; id: string } | { ok: false; code: string; text: string }

interface FunctionAnswer { ok?: boolean; code?: unknown; message?: string; id?: string | null }

function toResult(answer: FunctionAnswer | null): DownlinkResult {
  if (answer?.ok && answer.id) return { ok: true, id: answer.id }
  // The gateway's own 401 carries a numeric code.
  const code = typeof answer?.code === 'string' ? answer.code : 'error'
  return { ok: false, code, text: REFUSALS[code] ?? answer?.message ?? 'Not sent: something went wrong. Try again.' }
}

/**
 * Asks the function to read (no value) or set the camera's setting. A non-2xx answer still carries
 * the function's body, on the error's `context` (functions-js FunctionsHttpError).
 */
export async function requestDownlink(
  db: Pick<SupabaseClient, 'functions'>,
  deploymentId: string,
  request: { kind: 'op_get' } | { kind: 'op_set'; value: number },
  opIndex = CHECK_IN_OP,
): Promise<DownlinkResult> {
  const body = {
    deployment_id: deploymentId, kind: request.kind, op_index: opIndex,
    ...(request.kind === 'op_set' ? { op_value: request.value } : {}),
  }
  const { data, error } = await db.functions.invoke('lorawan-downlink', { body })
  if (!error) return toResult(data as FunctionAnswer | null)
  const ctx = (error as { context?: unknown }).context as { json?: () => Promise<unknown> } | undefined
  if (typeof ctx?.json === 'function') {
    const answer = await ctx.json().catch(() => null)
    if (answer && typeof answer === 'object') return toResult(answer as FunctionAnswer)
  }
  return { ok: false, code: 'unreachable', text: 'Could not reach the server. Try again.' }
}
