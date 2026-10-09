/**
 * Options for the review grid's deployment filter (#207).
 *
 * Deployments are often re-made at the same site, so their location names repeat ("User
 * Location" twice in the report). Each option names the location, the start date and, when the
 * grid spans several projects, the project, so two deployments never read the same.
 */

export interface FilterDeployment {
  id: string
  project_id: string
  location_name: string | null
  deployment_start?: string | null
  timezone?: string | null
}

export interface FilterOption {
  value: string
  label: string
}

const DATE_FORMAT = { day: 'numeric', month: 'short', year: 'numeric' } as const
const BROWSER_ZONE = new Intl.DateTimeFormat('en-GB', DATE_FORMAT)
const formatters = new Map<string, Intl.DateTimeFormat>()

/** One formatter per zone; an unknown zone falls back to the browser's. */
function dateFormatter(tz: string | null | undefined): Intl.DateTimeFormat {
  if (!tz) return BROWSER_ZONE
  let f = formatters.get(tz)
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('en-GB', { ...DATE_FORMAT, timeZone: tz })
    } catch {
      f = BROWSER_ZONE
    }
    formatters.set(tz, f)
  }
  return f
}

/** The start date in the deployment's own zone ("9 Jun 2026"), or null when unknown. */
export function startDateLabel(iso: string | null | undefined, tz?: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  return dateFormatter(tz).format(d)
}

/**
 * One option per deployment, sorted by location name, then newest start first. `projectNames`
 * maps project id to name; the project is added to a label only when the deployments span more
 * than one project. Labels that still collide get the id prefix.
 */
export function deploymentFilterOptions(
  deployments: FilterDeployment[],
  projectNames: ReadonlyMap<string, string> = new Map(),
): FilterOption[] {
  const multiProject = new Set(deployments.map(d => d.project_id)).size > 1
  const rows = deployments.map(d => {
    const name = d.location_name?.trim() || d.id.slice(0, 8)
    const parts = [name]
    const date = startDateLabel(d.deployment_start, d.timezone)
    if (date) parts.push(date)
    if (multiProject) parts.push(projectNames.get(d.project_id) ?? d.project_id.slice(0, 8))
    return { d, name, label: parts.join(' · '), start: d.deployment_start ? Date.parse(d.deployment_start) || 0 : 0 }
  })

  const seen = new Map<string, number>()
  for (const r of rows) seen.set(r.label, (seen.get(r.label) ?? 0) + 1)
  for (const r of rows) if ((seen.get(r.label) ?? 0) > 1) r.label = `${r.label} · ${r.d.id.slice(0, 8)}`

  rows.sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    || b.start - a.start
    || a.label.localeCompare(b.label))
  return rows.map(r => ({ value: r.d.id, label: r.label }))
}

/** Options whose label contains every word of `query`, ignoring case. An empty query keeps all. */
export function matchOptions<T extends FilterOption>(options: T[], query: string): T[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return options
  return options.filter(o => {
    const label = o.label.toLowerCase()
    return words.every(w => label.includes(w))
  })
}
