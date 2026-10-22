/**
 * The header project picker at scale (UI audit 01 B1, B4): a search box above ten projects,
 * groups by organisation when there is more than one, and a tooltip that names the selection.
 * Home lists projects in the picker's order with the selected ones first (01 C1).
 */
import type { SelectableProject } from './projectSelection'

/** Above this many projects the picker gets a search box. */
export const PICKER_SEARCH_MIN = 10

/** Projects whose name or organisation contains the query, ignoring case. */
export function filterProjects<P extends SelectableProject>(projects: P[], query: string): P[] {
  const q = query.trim().toLowerCase()
  if (!q) return projects
  return projects.filter(p =>
    p.name.toLowerCase().includes(q) || (p.organisation ?? '').toLowerCase().includes(q))
}

export interface ProjectGroup<P> {
  /** The organisation's name, null for a project whose organisation the user cannot read. */
  organisation: string | null
  projects: P[]
}

/**
 * One group per organisation, sorted by name with the unnamed group last, or a single unlabelled
 * group when every project is in the same organisation. Projects keep their order in a group.
 */
export function groupByOrganisation<P extends SelectableProject>(projects: P[]): ProjectGroup<P>[] {
  const groups = new Map<string | null, P[]>()
  for (const p of projects) {
    const key = p.organisation ?? null
    groups.set(key, [...(groups.get(key) ?? []), p])
  }
  if (groups.size <= 1) return [{ organisation: null, projects }]
  return [...groups.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : a.localeCompare(b)))
    .map(([organisation, list]) => ({ organisation, projects: list }))
}

/** The ticked projects' names for the picker's tooltip, cut after `max` names. */
export function projectPickerTitle(projects: SelectableProject[], selectedProjectIds: string[], max = 12): string {
  const selected = new Set(selectedProjectIds)
  const names = projects.filter(p => selected.has(p.id)).map(p => p.name)
  if (names.length === 0) return 'No project selected'
  const shown = names.slice(0, max).join('\n')
  return names.length > max ? `${shown}\nand ${names.length - max} more` : shown
}

/** The selected projects first, each part in the order given (the picker's, by name). */
export function selectedFirst<P extends { id: string }>(projects: P[], selectedProjectIds: string[]): P[] {
  const selected = new Set(selectedProjectIds)
  return [...projects.filter(p => selected.has(p.id)), ...projects.filter(p => !selected.has(p.id))]
}
