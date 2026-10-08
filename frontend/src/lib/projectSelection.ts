/**
 * The project picker's selection rule (#214).
 *
 * Every project is selected when the list loads, so an empty selection means none, never all.
 * Pages ask `projectQuery` which projects to query instead of keeping their own
 * `length > 0` check, which used to turn an empty selection into every project's data.
 */

export interface SelectableProject {
  id: string
  name: string
}

export interface SelectionState {
  /** The user the list was loaded for, so a different sign-in starts from scratch. */
  userId: string | null
  projects: SelectableProject[]
  selectedProjectIds: string[]
}

export const EMPTY_SELECTION: SelectionState = { userId: null, projects: [], selectedProjectIds: [] }

function allTicked(projects: SelectableProject[], selectedProjectIds: string[]): boolean {
  const selected = new Set(selectedProjectIds)
  return projects.every(p => selected.has(p.id))
}

/**
 * The selection after the project list (re)loads. A first load, or a different user, selects
 * everything. A reload (an accepted invitation) keeps "all" as all, new projects included, and
 * otherwise keeps the user's choice minus any project that has gone. `projects` is null when the
 * load failed: a reload keeps what it had, and a first load records the user with no projects so
 * the list does not read as loading forever.
 */
export function selectionAfterLoad(
  prev: SelectionState,
  userId: string,
  projects: SelectableProject[] | null,
): SelectionState {
  if (!projects) return prev.userId === userId ? prev : { ...EMPTY_SELECTION, userId }
  const ids = projects.map(p => p.id)
  const wasAll = prev.userId !== userId || allTicked(prev.projects, prev.selectedProjectIds)
  const present = new Set(ids)
  return {
    userId,
    projects,
    selectedProjectIds: wasAll ? ids : prev.selectedProjectIds.filter(id => present.has(id)),
  }
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
