/**
 * The reads behind the Realtime page (/field): the deployments it shows, each one's newest LoRaWAN
 * check-in, and the detections its camera counted in the last 48 hours. The Messages tab's reads
 * are in `lorawanFeed.ts`.
 *
 * - Deployments page past PostgREST's 1,000-row cap (`readAllPages`).
 * - Deployment ids go to the database in chunks of `ID_CHUNK`, so an `in.(...)` list stays well
 *   inside the URL limit however many cameras are out.
 * - Each deployment comes with its ping period (`lorawan_ping_minutes`, ww-backend#372) and the
 *   versions of the firmware it started with (`ble_firmware_id`, `himax_firmware_id`, read from
 *   `firmware`, which every signed-in user may read).
 * - Telemetry is one read of the view `deployment_latest_lorawan` per chunk: one row per
 *   deployment with the time of its newest message, the latest battery and SD free space, and
 *   the camera's health state and reasons (ww-backend #333, #358, #381), so a chatty camera
 *   cannot crowd a quiet one out (UI audit 04 #10). The view runs as the caller, so
 *   `lorawan_messages`' read policy decides which deployments come back.
 * - A new message re-reads only its own deployment (`fetchDeploymentUpdate`).
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { chunk, readAllPages } from './readAllPages'
import { toHealthState, type HealthState } from './cameraState'

export interface FieldDeployment {
  id: string
  project_id: string
  device_id: string | null
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  /** Minutes between the camera's pings: 0 is LoRaWAN off, null not recorded. */
  lorawan_ping_minutes: number | null
  project_name: string
  device_name: string
  /** The versions of the firmware the deployment started with. */
  ble_firmware: string | null
  himax_firmware: string | null
}

export interface Telemetry {
  /** The newest message of any kind. */
  lastSeen: string | null
  batteryMv: number | null
  /** Percent of the battery byte's scale, for older firmware with no `batteryMv`. */
  batteryPct: number | null
  sdFreePct: number | null
  health: HealthState | null
  /** `health_reasons` codes, worst first. */
  healthReasons: string[]
}

/** A deployment with no message yet. */
export const NO_MESSAGE: Telemetry = {
  lastSeen: null, batteryMv: null, batteryPct: null, sdFreePct: null, health: null, healthReasons: [],
}

/** Deployment ids per request. */
export const ID_CHUNK = 100
export const DETECTION_HOURS = 48

/** The query keys of the page's reads; a live update writes into the same entries. */
export const realtimeKeys = {
  all: ['realtime'] as const,
  deployments: (userId: string | undefined, projectIds: string[] | null) =>
    ['realtime', 'deployments', userId, projectIds] as const,
  telemetry: (ids: string[]) => ['realtime', 'telemetry', ids] as const,
  detections: (ids: string[]) => ['realtime', 'detections', ids] as const,
  feed: (ids: string[]) => ['realtime', 'feed', ids] as const,
}

type DeploymentRow = Omit<FieldDeployment, 'project_name' | 'device_name' | 'ble_firmware' | 'himax_firmware'> & {
  projects: { name: string } | null
  devices: { name: string } | null
  ble: { version: string } | null
  himax: { version: string } | null
}

/** Two keys from `deployments` to `firmware`, so each embed names its own. */
const FIELD_DEPLOYMENT_COLUMNS = 'id, project_id, device_id, location_name, latitude, longitude, deployment_start, '
  + 'deployment_end, lorawan_ping_minutes, projects(name), devices(name), '
  + 'ble:firmware!deployments_ble_firmware_id_fkey(version), himax:firmware!deployments_himax_firmware_id_fkey(version)'

/** "BLE 0.30.62 · Himax 1.2.0", whichever the deployment recorded; a dash for neither. */
export function firmwareText(d: Pick<FieldDeployment, 'ble_firmware' | 'himax_firmware'>): string {
  const parts = [d.ble_firmware && `BLE ${d.ble_firmware}`, d.himax_firmware && `Himax ${d.himax_firmware}`]
  return parts.filter(Boolean).join(' · ') || '-'
}

/** Midnight at the start of `now`'s day, in the viewer's zone. */
export function startOfLocalDay(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

/**
 * The deployments the page shows: no end date, or one that falls today (the viewer's day) or
 * later. One that ended today stays on the page as Finished; older ones do not show.
 */
export async function fetchFieldDeployments(
  db: SupabaseClient, projectIds: string[], now = new Date(),
): Promise<FieldDeployment[]> {
  // No projects, no request, as the chunked reads below do.
  if (projectIds.length === 0) return []
  const today = startOfLocalDay(now).toISOString()
  const rows = await readAllPages((from, to) => db
    .from('deployments')
    .select(FIELD_DEPLOYMENT_COLUMNS)
    .is('deleted_at', null)
    .or(`deployment_end.is.null,deployment_end.gte.${today}`)
    .in('project_id', projectIds)
    .order('deployment_start', { ascending: true })
    .order('id')
    .range(from, to))
  return (rows as unknown as DeploymentRow[]).map(({ projects, devices, ble, himax, ...d }) => ({
    ...d, project_name: projects?.name ?? '-', device_name: devices?.name ?? '-',
    ble_firmware: ble?.version ?? null, himax_firmware: himax?.version ?? null,
  }))
}

interface LatestMessageRow {
  deployment_id: string
  received_at: string
  battery_level: number | null
  battery_mv: number | null
  sd_free_pct: number | null
  health_state: string | null
  health_reasons: string[] | null
}

const TELEMETRY_COLUMNS =
  'deployment_id, received_at, battery_level, battery_mv, sd_free_pct, health_state, health_reasons'

function toTelemetry(m: LatestMessageRow): Telemetry {
  return {
    lastSeen: m.received_at, batteryMv: m.battery_mv, batteryPct: m.battery_level, sdFreePct: m.sd_free_pct,
    health: toHealthState(m.health_state), healthReasons: m.health_reasons ?? [],
  }
}

/** The newest check-in of each deployment that has one. */
export async function fetchLatestTelemetry(
  db: SupabaseClient, deploymentIds: string[],
): Promise<Record<string, Telemetry>> {
  const reads = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => db
    .from('deployment_latest_lorawan')
    .select(TELEMETRY_COLUMNS)
    .in('deployment_id', ids)))
  const out: Record<string, Telemetry> = {}
  for (const { data, error } of reads) {
    if (error) throw new Error(error.message)
    for (const m of (data ?? []) as LatestMessageRow[]) {
      out[m.deployment_id] = toTelemetry(m)
    }
  }
  return out
}

interface DetectionMessageRow {
  deployment_id: string
  lorawan_parsed_messages: { nn_detections: number | null }[]
}

/** Detections per deployment: the sum of `nn_detections` over its messages. */
export function sumDetections(rows: DetectionMessageRow[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const m of rows) {
    for (const p of m.lorawan_parsed_messages) {
      if (p.nn_detections) out[m.deployment_id] = (out[m.deployment_id] ?? 0) + p.nn_detections
    }
  }
  return out
}

/**
 * What each deployment's camera counted in the last `DETECTION_HOURS`, from its LoRaWAN messages
 * (`lorawan_parsed_messages.nn_detections`, the rise in the AI processor's positive count, set by
 * ww-backend's trigger). Only messages with a detection are read.
 */
export async function fetchLorawanDetections(
  db: SupabaseClient, deploymentIds: string[], now = Date.now(),
): Promise<Record<string, number>> {
  const since = new Date(now - DETECTION_HOURS * 3_600_000).toISOString()
  const reads = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => readAllPages((from, to) => db
    .from('lorawan_messages')
    .select('id, deployment_id, lorawan_parsed_messages!inner(nn_detections)')
    .in('deployment_id', ids)
    .gte('received_at', since)
    .gt('lorawan_parsed_messages.nn_detections', 0)
    .order('id')
    .range(from, to))))
  return sumDetections(reads.flat() as unknown as DetectionMessageRow[])
}

export interface DeploymentUpdate {
  telemetry: Telemetry | undefined
  detections: number | undefined
}

/** One deployment's newest check-in and recent detections, read again after it sends a message. */
export async function fetchDeploymentUpdate(
  db: SupabaseClient, deploymentId: string, now = Date.now(),
): Promise<DeploymentUpdate> {
  const [telemetry, detections] = await Promise.all([
    fetchLatestTelemetry(db, [deploymentId]),
    fetchLorawanDetections(db, [deploymentId], now),
  ])
  return { telemetry: telemetry[deploymentId], detections: detections[deploymentId] }
}

/** `map` with `id` set to `value`, or without `id` when `value` is undefined. */
export function withEntry<T>(map: Record<string, T>, id: string, value: T | undefined): Record<string, T> {
  const next = { ...map }
  if (value === undefined) delete next[id]
  else next[id] = value
  return next
}

/**
 * `map` with deployment `id`'s last message at `receivedAt`, when that is newer. Any message counts,
 * an empty frame too, which has no decoded values for a re-read to find.
 */
export function withLastSeen(
  map: Record<string, Telemetry>, id: string, receivedAt: string,
): Record<string, Telemetry> {
  const at = Date.parse(receivedAt)
  const old = map[id]
  if (Number.isNaN(at) || (old?.lastSeen && Date.parse(old.lastSeen) >= at)) return map
  return { ...map, [id]: { ...(old ?? NO_MESSAGE), lastSeen: receivedAt } }
}
