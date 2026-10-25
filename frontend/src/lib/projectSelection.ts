/**
 * The project picker's selection rule (#214).
 *
 * The first load selects one project, the one with the most recent deployment
 * (`defaultProjectId`), so an empty selection means none, never all.
 * Pages ask `projectQuery` which projects to query instead of keeping their own
 * `length > 0` check, which used to turn an empty selection into every project's data.
 */

export interface SelectableProject {
  id: string
  name: string
  /** The organisation's name, for grouping the picker; null when the user cannot read it. */
  organisation?: string | null
  /** When the project's newest live deployment started; null when it has none. */
  latestDeploymentStart?: string | null
  createdAt?: string | null
}

export interface SelectionState {
  /** The user the list was loaded for, so a different sign-in starts from scratch. */
  userId: string | null
  projects: SelectableProject[]
  selectedProjectIds: string[]
}

export const EMPTY_SELECTION: SelectionState = { userId: null, projects: [], selectedProjectIds: [] }

/**
 * The projects columns the picker reads. The organisation embed is null when RLS hides it; the
 * query narrows the deployments embed to each project's newest live deployment (useProjectSelection).
 */
export const PROJECT_LIST_COLUMNS = 'id, name, created_at, organisations(name), deployments(deployment_start)'

/** A `PROJECT_LIST_COLUMNS` row as a picker project. PostgREST returns a to-one embed as an object. */
export function toSelectableProject(row: {
  id: string; name: string; organisations?: unknown; deployments?: unknown; created_at?: unknown
}): SelectableProject {
  const org = Array.isArray(row.organisations) ? row.organisations[0] : row.organisations
  const name = org && typeof org === 'object' && 'name' in org ? (org as { name: unknown }).name : null
  const newest = Array.isArray(row.deployments) ? row.deployments[0] : null
  const start = newest && typeof newest === 'object' && 'deployment_start' in newest
    ? (newest as { deployment_start: unknown }).deployment_start
    : null
  return {
    id: row.id,
    name: row.name,
    organisation: typeof name === 'string' ? name : null,
    latestDeploymentStart: typeof start === 'string' ? start : null,
    createdAt: typeof row.created_at === 'string' ? row.created_at : null,
  }
}

const instant = (iso?: string | null) => {
  const t = iso ? Date.parse(iso) : NaN
  return Number.isNaN(t) ? -Infinity : t
}

/**
 * The project a first load selects: the one whose newest deployment started last; among projects
 * with no deployment, the newest; otherwise the first in the list. Null with no projects.
 */
export function defaultProjectId(projects: SelectableProject[]): string | null {
  let best: SelectableProject | null = null
  for (const p of projects) {
    if (!best) { best = p; continue }
    const byStart = instant(p.latestDeploymentStart) - instant(best.latestDeploymentStart)
    const later = byStart > 0
      || ((byStart === 0 || Number.isNaN(byStart)) && instant(p.createdAt) > instant(best.createdAt))
    if (later) best = p
  }
  return best?.id ?? null
}

function allTicked(projects: SelectableProject[], selectedProjectIds: string[]): boolean {
  const selected = new Set(selectedProjectIds)
  return projects.every(p => selected.has(p.id))
}

/**
 * The selection after the project list (re)loads. A first load, a different user, or a first list
 * after an empty one selects the default project (`defaultProjectId`). A reload (an accepted
 * invitation, a return to the tab, a project created, deleted or restored in Settings) keeps "all"
 * as all, new projects included, and otherwise keeps the user's choice minus any project that has
 * gone. `projects` is null when the
 * load failed: a reload keeps what it had, and a first load records the user with no projects so
 * the list does not read as loading forever.
 *
 * A reload that changes nothing returns `prev` itself. The list refetches whenever the user
 * returns to the tab (#299), and pages key their queries on the selected ids, so a new but equal
 * array would refetch every page's data each time.
 */
export function selectionAfterLoad(
  prev: SelectionState,
  userId: string,
  projects: SelectableProject[] | null,
): SelectionState {
  if (!projects) return prev.userId === userId ? prev : { ...EMPTY_SELECTION, userId }
  const ids = projects.map(p => p.id)
  const present = new Set(ids)
  const firstLoad = prev.userId !== userId || prev.projects.length === 0
  const first = firstLoad ? defaultProjectId(projects) : null
  const selectedProjectIds = firstLoad
    ? (first ? [first] : [])
    : allTicked(prev.projects, prev.selectedProjectIds) ? ids : prev.selectedProjectIds.filter(id => present.has(id))
  const next = { userId, projects, selectedProjectIds }
  return sameSelection(prev, next) ? prev : next
}

function sameSelection(a: SelectionState, b: SelectionState): boolean {
  return a.userId === b.userId
    && a.projects.length === b.projects.length
    && a.projects.every((p, i) => p.id === b.projects[i].id && p.name === b.projects[i].name
      && (p.organisation ?? null) === (b.projects[i].organisation ?? null))
    && a.selectedProjectIds.length === b.selectedProjectIds.length
    && a.selectedProjectIds.every((id, i) => id === b.selectedProjectIds[i])
}

export function toggleProjectSelection(state: SelectionState, id: string): SelectionState {
  const prev = state.selectedProjectIds
  return { ...state, selectedProjectIds: prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id] }
}

export function selectAllProjects(state: SelectionState): SelectionState {
  return { ...state, selectedProjectIds: state.projects.map(p => p.id) }
}

/** Clear means none: the pages show their empty state, not every project. */
export function clearProjects(state: SelectionState): SelectionState {
  return { ...state, selectedProjectIds: [] }
}

export interface ProjectQuery {
  /** The project ids to filter on, or null when there is nothing to query yet or at all. */
  ids: string[] | null
  /** The list has loaded and nothing is ticked: show the empty state and run no query. */
  none: boolean
}

export function projectQuery(selectedProjectIds: string[], isLoading: boolean): ProjectQuery {
  if (isLoading) return { ids: null, none: false }
  if (selectedProjectIds.length === 0) return { ids: null, none: true }
  return { ids: selectedProjectIds, none: false }
}

export function projectPickerLabel(projects: SelectableProject[], selectedProjectIds: string[]): string {
  if (selectedProjectIds.length === 0) return 'No project selected'
  if (allTicked(projects, selectedProjectIds)) return 'All Projects'
  if (selectedProjectIds.length === 1) {
    return projects.find(p => p.id === selectedProjectIds[0])?.name || '1 Project'
  }
  return `${selectedProjectIds.length} Projects`
}
