/**
 * Where an upload can go: the deployments of the projects the user may write to, read past the
 * 1,000-row cap, and the grouped, searchable options the triage pickers show (UI audit 05 #6, #7).
 *
 * Viewers do not upload (decision 1 of the 2026-10 UI audit), so the read is scoped to the
 * projects `useProjectRoles().canWrite` allows; a claim that matches none of these deployments
 * but that /api/deployments/validate reports as visible is in a project the user only views.
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { deploymentFilterOptions, matchOptions, type FilterOption } from './deploymentFilterOptions'
import { PAGE_SIZE } from './liveObservations'

/** The deployment columns the upload page reads. */
export const UPLOAD_DEPLOYMENT_COLUMNS =
  'id, project_id, name, location_name, latitude, longitude, deployment_start, timezone'

/** Project ids per request, so the `in` filter stays well inside a URL. */
const PROJECT_CHUNK = 100

export interface UploadTarget {
  id: string
  project_id: string
  name: string | null
  location_name: string | null
  deployment_start: string | null
  timezone?: string | null
}

/**
 * Every live deployment in `projectIds`, page by page until a short page. An error stops the
 * read and is returned: a partial list would send real photos to triage as "not found".
 */
export async function fetchDeploymentsInProjects<T extends UploadTarget>(
  db: SupabaseClient,
  projectIds: string[],
): Promise<{ data: T[] | null; error: { message: string } | null }> {
  const out: T[] = []
  for (let c = 0; c < projectIds.length; c += PROJECT_CHUNK) {
    const chunk = projectIds.slice(c, c + PROJECT_CHUNK)
    for (let from = 0; ; from += PAGE_SIZE) {
      const { data, error } = await db
        .from('deployments')
        .select(UPLOAD_DEPLOYMENT_COLUMNS)
        .in('project_id', chunk)
        .is('deleted_at', null)
        .order('id')
        .range(from, from + PAGE_SIZE - 1)
      if (error) return { data: null, error }
      const page = (data ?? []) as unknown as T[]
      out.push(...page)
      if (page.length < PAGE_SIZE) break
    }
  }
  return { data: out, error: null }
}

/** One project's deployments in a picker. */
export interface PickerGroup {
  projectId: string
  projectName: string
  options: FilterOption[]
}

/**
 * Picker options grouped by project, projects by name. Within a group each option is labelled as
 * the review grid's deployment filter labels it (location, start date, id prefix on a clash).
 */
export function deploymentPickerGroups(
  deployments: UploadTarget[],
  projects: { id: string; name: string }[],
): PickerGroup[] {
  const byProject = new Map<string, UploadTarget[]>()
  for (const d of deployments) {
    const list = byProject.get(d.project_id)
    if (list) list.push(d)
    else byProject.set(d.project_id, [d])
  }
  const names = new Map(projects.map((p) => [p.id, p.name]))
  return Array.from(byProject, ([projectId, list]) => ({
    projectId,
    projectName: names.get(projectId) ?? projectId.slice(0, 8),
    options: deploymentFilterOptions(list.map((d) => ({ ...d, location_name: d.location_name || d.name || null }))),
  })).sort((a, b) => a.projectName.localeCompare(b.projectName, undefined, { sensitivity: 'base' }))
}

/** The groups narrowed to options matching every word of `query`; empty groups drop out. */
export function filterPickerGroups(groups: PickerGroup[], query: string): PickerGroup[] {
  if (!query.trim()) return groups
  return groups.flatMap((g) => {
    const inProject = g.projectName.toLowerCase().includes(query.trim().toLowerCase())
    const options = inProject ? g.options : matchOptions(g.options, query)
    return options.length ? [{ ...g, options }] : []
  })
}

/** Each deployment's picker label, with its project when the picker spans several. */
export function pickerLabels(groups: PickerGroup[]): Map<string, string> {
  const withProject = groups.length > 1
  const out = new Map<string, string>()
  for (const g of groups) {
    for (const o of g.options) out.set(o.value, withProject ? `${o.label} · ${g.projectName}` : o.label)
  }
  return out
}

/** How many options the groups hold. */
export function optionCount(groups: PickerGroup[]): number {
  return groups.reduce((n, g) => n + g.options.length, 0)
}
