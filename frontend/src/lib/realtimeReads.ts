/**
 * The reads behind the Realtime page (/field): the active deployments of the selected projects,
 * each one's newest LoRaWAN check-in, and its animal detections in the last 48 hours.
 *
 * - Deployments page past PostgREST's 1,000-row cap (`readAllPages`).
 * - Deployment ids go to the database in chunks of `ID_CHUNK`, so an `in.(...)` list stays well
 *   inside the URL limit however many cameras are out.
 * - Telemetry is one read of the view `deployment_latest_lorawan` per chunk: one row per
 *   deployment with its newest message's battery level and SD card use (ww-backend #333), so a
 *   chatty camera cannot crowd a quiet one out (UI audit 04 #10). The view runs as the caller,
 *   so `lorawan_messages`' read policy decides which deployments come back.
 * - A new message re-reads only its own deployment (`fetchDeploymentUpdate`).
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchLiveObservations } from './liveObservations'
import { chunk, readAllPages } from './readAllPages'

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
export const DETECTION_HOURS = 48

type DeploymentRow = Omit<ActiveDeployment, 'project_name' | 'device_name'> & {
  projects: { name: string } | null
  devices: { name: string } | null
}

/** Active means no end date, or an end date still to come. */
export async function fetchActiveDeployments(
  db: SupabaseClient, projectIds: string[], now = new Date(),
): Promise<ActiveDeployment[]> {
  // No projects, no request, as the chunked reads below do.
  if (projectIds.length === 0) return []
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

interface LatestMessageRow {
  deployment_id: string
  received_at: string
  battery_level: number | null
  sd_card_used_capacity: number | null
}

/** The newest check-in of each deployment that has one. */
export async function fetchLatestTelemetry(
  db: SupabaseClient, deploymentIds: string[],
): Promise<Record<string, Telemetry>> {
  const reads = await Promise.all(chunk(deploymentIds, ID_CHUNK).map(ids => db
    .from('deployment_latest_lorawan')
    .select('deployment_id, received_at, battery_level, sd_card_used_capacity')
    .in('deployment_id', ids)))
  const out: Record<string, Telemetry> = {}
  for (const { data, error } of reads) {
    if (error) throw new Error(error.message)
    for (const m of (data ?? []) as LatestMessageRow[]) {
      out[m.deployment_id] = { battery: m.battery_level, sdUsed: m.sd_card_used_capacity, lastSeen: m.received_at }
    }
  }
  return out
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

export interface DeploymentUpdate {
  telemetry: Telemetry | undefined
  detections: RecentDetections | undefined
}

/** One deployment's newest check-in and recent detections, read again after it sends a message. */
export async function fetchDeploymentUpdate(
  db: SupabaseClient, deploymentId: string, now = Date.now(),
): Promise<DeploymentUpdate> {
  const [telemetry, detections] = await Promise.all([
    fetchLatestTelemetry(db, [deploymentId]),
    fetchRecentDetections(db, [deploymentId], now),
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
