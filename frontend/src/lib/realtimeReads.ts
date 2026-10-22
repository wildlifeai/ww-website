/**
 * The reads behind the Realtime page (/field): the active deployments of the selected projects,
 * each one's newest LoRaWAN check-in, and its animal detections in the last 48 hours.
 *
 * - Deployments page past PostgREST's 1,000-row cap (`readAllPages`).
 * - Deployment ids go to the database in chunks of `ID_CHUNK`, so an `in.(...)` list stays well
 *   inside the URL limit however many cameras are out.
 * - Telemetry takes the newest `TELEMETRY_WINDOW` messages of a chunk in one read. A camera that
 *   reports every few minutes can fill that window, so when it comes back full, the deployments
 *   it missed get their newest message in a second read, embedded under `deployments` with a
 *   limit of one. Without that a quiet camera showed "Awaiting" instead of "Silent" (UI audit
 *   04 #10). A view or RPC returning the newest message per deployment would make this one
 *   indexed read; that is a database need, raised with the maintainer.
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchLiveObservations } from './liveObservations'
import { readAllPages } from './readAllPages'

export interface ActiveDeployment {
  id: string
  project_id: string
  device_id: string | null
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  project_name: string
  device_name: string
}

export interface Telemetry {
  battery: number | null
  sdUsed: number | null
  lastSeen: string | null
}

export interface RecentDetections {
  count: number
  latest: string | null
  /** The species of the latest detection. */
  top: string | null
}

/** Deployment ids per request. */
export const ID_CHUNK = 100
/** Newest messages read per chunk before falling back to one read per missed deployment. */
export const TELEMETRY_WINDOW = 1000
export const DETECTION_HOURS = 48

export function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

type DeploymentRow = Omit<ActiveDeployment, 'project_name' | 'device_name'> & {
  projects: { name: string } | null
  devices: { name: string } | null
}

/** Active means no end date, or an end date still to come. */
export async function fetchActiveDeployments(
  db: SupabaseClient, projectIds: string[], now = new Date(),
): Promise<ActiveDeployment[]> {
  const nowIso = now.toISOString()
  const rows = await readAllPages((from, to) => db
    .from('deployments')
    .select('id, project_id, device_id, location_name, latitude, longitude, deployment_start, deployment_end, projects(name), devices(name)')
    .is('deleted_at', null)
    .or(`deployment_end.is.null,deployment_end.gt.${nowIso}`)
    .in('project_id', projectIds)
    .order('deployment_start', { ascending: true })
    .order('id')
    .range(from, to))
  return (rows as unknown as DeploymentRow[]).map(({ projects, devices, ...d }) => ({
    ...d, project_name: projects?.name ?? '-', device_name: devices?.name ?? '-',
  }))
}

interface ParsedMessage {
  battery_level: number | null
  sd_card_used_capacity: number | null
}

export interface MessageRow {
  deployment_id: string | null
  received_at: string | null
  lorawan_parsed_messages: ParsedMessage | ParsedMessage[] | null
}

function toTelemetry(m: Omit<MessageRow, 'deployment_id'>): Telemetry {
  const p = Array.isArray(m.lorawan_parsed_messages) ? m.lorawan_parsed_messages[0] : m.lorawan_parsed_messages
  return { battery: p?.battery_level ?? null, sdUsed: p?.sd_card_used_capacity ?? null, lastSeen: m.received_at ?? null }
}

/** The first row of each deployment wins, so pass the rows newest first. */
export function newestPerDeployment(rows: MessageRow[]): Record<string, Telemetry> {
  const out: Record<string, Telemetry> = {}
  for (const m of rows) {
    if (!m.deployment_id || out[m.deployment_id]) continue
    out[m.deployment_id] = toTelemetry(m)
  }
  return out
}

const MESSAGE_COLUMNS = 'received_at, lorawan_parsed_messages(battery_level, sd_card_used_capacity)'

/** The newest check-in of each deployment that has one. */
export async function fetchLatestTelemetry(
  db: SupabaseClient, deploymentIds: string[],
): Promise<Record<string, Telemetry>> {
  const parts = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => latestForChunk(db, ids)))
  return Object.assign({}, ...parts)
}

async function latestForChunk(db: SupabaseClient, ids: string[]): Promise<Record<string, Telemetry>> {
  const { data, error } = await db
    .from('lorawan_messages')
    .select(`deployment_id, ${MESSAGE_COLUMNS}`)
    .in('deployment_id', ids)
    .order('received_at', { ascending: false })
    .limit(TELEMETRY_WINDOW)
  if (error) throw new Error(error.message)
  const rows = (data ?? []) as unknown as MessageRow[]
  const latest = newestPerDeployment(rows)
  // A short window holds every message, so a deployment missing from it has none.
  if (rows.length < TELEMETRY_WINDOW) return latest
  const missed = ids.filter(id => !latest[id])
  if (missed.length === 0) return latest
  const more = await db
    .from('deployments')
    .select(`id, lorawan_messages(${MESSAGE_COLUMNS})`)
    .in('id', missed)
    .order('received_at', { referencedTable: 'lorawan_messages', ascending: false })
    .limit(1, { referencedTable: 'lorawan_messages' })
  if (more.error) throw new Error(more.error.message)
  for (const d of (more.data ?? []) as unknown as { id: string; lorawan_messages: Omit<MessageRow, 'deployment_id'>[] | null }[]) {
    const newest = d.lorawan_messages?.[0]
    if (newest) latest[d.id] = toTelemetry(newest)
  }
  return latest
}

interface DetectionRow {
  deployment_id: string
  scientific_name: string | null
  created_at: string
}

export function summariseDetections(rows: DetectionRow[]): Record<string, RecentDetections> {
  const out: Record<string, RecentDetections> = {}
  for (const o of rows) {
    const e = out[o.deployment_id] ?? { count: 0, latest: null, top: null }
    e.count += 1
    if (!e.latest || o.created_at > e.latest) { e.latest = o.created_at; e.top = o.scientific_name ?? e.top }
    out[o.deployment_id] = e
  }
  return out
}

/** Animal detections of each deployment in the last `DETECTION_HOURS`. */
export async function fetchRecentDetections(
  db: SupabaseClient, deploymentIds: string[], now = Date.now(),
): Promise<Record<string, RecentDetections>> {
  const since = new Date(now - DETECTION_HOURS * 3_600_000).toISOString()
  const reads = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => fetchLiveObservations<DetectionRow>(db, {
    columns: 'deployment_id, scientific_name, created_at',
    filter: q => q.in('deployment_id', ids).eq('observation_type', 'animal').gte('created_at', since),
  })))
  const failed = reads.find(r => r.error)
  if (failed?.error) throw new Error(failed.error.message)
  return summariseDetections(reads.flatMap(r => r.data ?? []))
}
