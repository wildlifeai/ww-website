// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The reads behind Insights (UI audit 04 #8, #9): the selected projects' deployments, and their
// live observations as the database counts them (ww-backend #334), never the observations
// themselves. Each pages past PostgREST's 1,000-row cap; the counts are POSTed to the functions,
// so no request grows with the number of deployments.
import type { SupabaseClient } from '@supabase/supabase-js'
import type { DailyCount, ObservationCount } from './observationSummary'
import { readAllPages } from './readAllPages'

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

type Counted<T> = Omit<T, 'n'> & { n: number | string }

/**
 * The live observations of the listed deployments, counted by the database per deployment,
 * species and type (`insights_observation_counts`, ww-backend #334): one request over their
 * projects, however many deployments they hold, and one more per further 1,000 rows. With
 * `narrow` (no project selected, deep links only) it asks for the listed deployments alone. A
 * deep-linked deployment from a project that is not selected brings that project's other
 * deployments too; they are dropped here, so the counts are those of the list.
 */
export async function fetchObservationCounts(
  db: SupabaseClient, deployments: Pick<InsightsDeployment, 'id' | 'project_id'>[], narrow: boolean,
): Promise<ObservationCount[]> {
  if (deployments.length === 0) return []
  const ids = new Set(deployments.map(d => d.id))
  const args = {
    p_project_ids: [...new Set(deployments.map(d => d.project_id))],
    ...(narrow ? { p_deployment_ids: [...ids] } : {}),
  }
  const rows = await readAllPages<Counted<ObservationCount>>((from, to) => db
    .rpc('insights_observation_counts', args)
    .order('deployment_id').order('scientific_name').order('observation_type')
    .range(from, to))
  const counts: ObservationCount[] = []
  for (const r of rows) if (ids.has(r.deployment_id)) counts.push({ ...r, n: Number(r.n) })
  return counts
}

/** The browser's zone, which dates the photos of a deployment that has none. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

/**
 * The deployment's named live observations per capture day and species, dated by the database
 * (`deployment_daily_counts`, ww-backend #334) in the deployment's zone, or in `timeZone` (the
 * page's, the browser's when empty) where it has none. Every species: the species filter
 * applies to the rows, so changing it reads nothing.
 */
export async function fetchDailyCounts(
  db: SupabaseClient, deploymentId: string, timeZone?: string | null,
): Promise<DailyCount[]> {
  const args = { p_deployment_ids: [deploymentId], p_fallback_tz: timeZone || browserTimeZone() }
  const rows = await readAllPages<Counted<DailyCount>>((from, to) => db
    .rpc('deployment_daily_counts', args)
    .order('deployment_id').order('day').order('scientific_name')
    .range(from, to))
  return rows.map(r => ({ ...r, n: Number(r.n) }))
}
