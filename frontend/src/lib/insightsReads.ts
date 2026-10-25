// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The two reads behind Insights (UI audit 04 #8, #9): the selected projects' deployments and
// their live observations. Both page past PostgREST's 1,000-row cap, and the observation read
// splits a long deployment list so no request URL grows with the number of deployments.
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchLiveObservations } from './liveObservations'

/** Rows per request: PostgREST's cap. */
export const READ_PAGE = 1000
/** Deployment ids per observation request (about 5.5 KB of URL). */
export const IDS_PER_REQUEST = 150

type PageResult<T> = { data: T[] | null; error: { message: string } | null }

/** Every row of a read, one `.range()` page at a time until a short page. */
export async function readAllPages<T>(
  page: (from: number, to: number) => PromiseLike<PageResult<T>>,
  size = READ_PAGE,
): Promise<T[]> {
  const rows: T[] = []
  for (let from = 0; ; from += size) {
    const { data, error } = await page(from, from + size - 1)
    if (error) throw new Error(error.message)
    const got = data ?? []
    rows.push(...got)
    if (got.length < size) return rows
  }
}

/** `items` in runs of at most `size`. */
export function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

export interface InsightsDeployment {
  id: string
  project_id: string
  project_name: string
  device_name: string
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  created_at: string
  timezone: string | null
}

const DEPLOYMENT_COLUMNS =
  'id, project_id, location_name, latitude, longitude, deployment_start, deployment_end, created_at, timezone, projects(name), devices(name)'

/**
 * The selected projects' deployments, newest first. Deployments named in `deploymentIds` (a
 * deep link) are always included, even when their project is not selected, so a link never
 * lands on an empty report; with no project ids they are the only ones.
 */
export async function fetchInsightsDeployments(
  db: SupabaseClient, projectIds: string[] | null, deploymentIds: string[],
): Promise<InsightsDeployment[]> {
  if (!projectIds && deploymentIds.length === 0) return []
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rows = await readAllPages<any>((from, to) => {
    let q = db.from('deployments').select(DEPLOYMENT_COLUMNS).is('deleted_at', null)
    if (!projectIds) q = q.in('id', deploymentIds)
    else if (deploymentIds.length > 0) q = q.or(`project_id.in.(${projectIds.join(',')}),id.in.(${deploymentIds.join(',')})`)
    else q = q.in('project_id', projectIds)
    return q.order('created_at', { ascending: false }).order('id').range(from, to)
  })
  return rows.map(({ projects, devices, ...d }) => ({
    ...d,
    project_name: projects?.name ?? '-',
    device_name: devices?.name ?? '-',
  }))
}

/** The deployment's live photos, as a head count (no rows read). A failure throws, so it never
 *  shows as a false zero. */
export async function countDeploymentPhotos(db: SupabaseClient, deploymentId: string): Promise<number> {
  const { count, error } = await db
    .from('media')
    .select('id', { count: 'exact', head: true })
    .eq('deployment_id', deploymentId)
    .is('deleted_at', null)
  if (error) throw new Error(error.message)
  return count ?? 0
}

export interface InsightsObservation {
  id: string
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  /** When the photo was taken (`media.timestamp`, UTC); null with no photo or no capture time. */
  captured_at: string | null
}

type Embedded = { timestamp: string | null } | null
type ObservationRow = Omit<InsightsObservation, 'captured_at'> & { media?: Embedded | Embedded[] }

/**
 * Every live observation of the deployments, read in runs of `IDS_PER_REQUEST` ids, with its
 * photo's capture time embedded in the same read (no request per row).
 */
export async function fetchInsightsObservations(
  db: SupabaseClient, deploymentIds: string[],
): Promise<InsightsObservation[]> {
  const parts = await Promise.all(chunk(deploymentIds, IDS_PER_REQUEST).map(async ids => {
    const { data, error } = await fetchLiveObservations<ObservationRow>(db, {
      columns: 'id, deployment_id, scientific_name, observation_type',
      mediaColumns: 'timestamp',
      filter: q => q.in('deployment_id', ids),
    })
    if (error) throw new Error(error.message)
    return (data ?? []).map(({ media, ...o }) => {
      const photo = Array.isArray(media) ? media[0] : media
      return { ...o, captured_at: photo?.timestamp ?? null }
    })
  }))
  return parts.flat()
}
