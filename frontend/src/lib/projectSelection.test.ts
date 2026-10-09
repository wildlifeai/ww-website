import { describe, expect, it } from 'vitest'
import {
  EMPTY_SELECTION,
  clearProjects,
  projectPickerLabel,
  projectQuery,
  selectAllProjects,
  selectionAfterLoad,
  toggleProjectSelection,
  type SelectionState,
} from './projectSelection'

const A = { id: 'a', name: 'Alpha' }
const B = { id: 'b', name: 'Bravo' }
const C = { id: 'c', name: 'Charlie' }

const state = (selectedProjectIds: string[], projects = [A, B], userId = 'u1'): SelectionState =>
  ({ userId, projects, selectedProjectIds })

describe('selectionAfterLoad', () => {
  it('selects every project when the list first loads after sign-in', () => {
    expect(selectionAfterLoad(EMPTY_SELECTION, 'u1', [A, B])).toEqual(state(['a', 'b']))
  })

  it('selects every project for a different user, whatever the last one had ticked', () => {
    expect(selectionAfterLoad(state([]), 'u2', [C]).selectedProjectIds).toEqual(['c'])
  })

  it('keeps all as all on a reload, a newly joined project included', () => {
    expect(selectionAfterLoad(state(['a', 'b']), 'u1', [A, B, C]).selectedProjectIds).toEqual(['a', 'b', 'c'])
  })

  it('keeps a cleared selection empty on a reload', () => {
    expect(selectionAfterLoad(state([]), 'u1', [A, B, C]).selectedProjectIds).toEqual([])
  })

  it('keeps a partial selection on a reload and drops projects that have gone', () => {
    expect(selectionAfterLoad(state(['a']), 'u1', [A, C]).selectedProjectIds).toEqual(['a'])
    expect(selectionAfterLoad(state(['b']), 'u1', [A, C]).selectedProjectIds).toEqual([])
  })

  it('keeps a project picked before the list caught up with it', () => {
    // Settings and Home pick one project with clearAll + toggleProject, possibly a new one.
    expect(selectionAfterLoad(state(['c']), 'u1', [A, B, C]).selectedProjectIds).toEqual(['c'])
  })

  it('returns the same state when a reload changes nothing, so pages do not refetch (#299)', () => {
    const prev = state(['a'])
    expect(selectionAfterLoad(prev, 'u1', [{ ...A }, { ...B }])).toBe(prev)
    const all = state(['a', 'b'])
    expect(selectionAfterLoad(all, 'u1', [A, B])).toBe(all)
  })

  it('returns a new state when a project is renamed or arrives', () => {
    const prev = state(['a'])
    expect(selectionAfterLoad(prev, 'u1', [{ ...A, name: 'Alpha 2' }, B]).projects[0].name).toBe('Alpha 2')
    const arrived = selectionAfterLoad(prev, 'u1', [A, B, C])
    expect(arrived).not.toBe(prev)
    expect(arrived.projects).toEqual([A, B, C])
  })

  it('selects a project that arrives on a tab-return refetch only when all were selected (#299)', () => {
    // A project created in the mobile app syncs while the website tab is in the background.
    expect(selectionAfterLoad(state(['a', 'b']), 'u1', [A, B, C]).selectedProjectIds).toEqual(['a', 'b', 'c'])
    expect(selectionAfterLoad(state(['b']), 'u1', [A, B, C]).selectedProjectIds).toEqual(['b'])
    expect(selectionAfterLoad(state([]), 'u1', [A, B, C]).selectedProjectIds).toEqual([])
  })

  it('selects the first project of a user who had none, since an empty list is all ticked', () => {
    expect(selectionAfterLoad(state([], []), 'u1', [C]).selectedProjectIds).toEqual(['c'])
  })

  it('keeps the selection when a reload fails, and settles a failed first load as no projects', () => {
    const prev = state(['a'])
    expect(selectionAfterLoad(prev, 'u1', null)).toBe(prev)
    expect(selectionAfterLoad(EMPTY_SELECTION, 'u1', null)).toEqual(state([], []))
  })
})

describe('clear, select all and toggle', () => {
  it('clear leaves nothing selected, which the pages read as none', () => {
    const cleared = clearProjects(state(['a', 'b']))
    expect(cleared.selectedProjectIds).toEqual([])
    expect(projectQuery(cleared.selectedProjectIds, false).none).toBe(true)
    expect(projectPickerLabel(cleared.projects, cleared.selectedProjectIds)).toBe('No project selected')
  })

  it('select all ticks every project in the list', () => {
    const all = selectAllProjects(state([]))
    expect(all.selectedProjectIds).toEqual(['a', 'b'])
    expect(projectQuery(all.selectedProjectIds, false)).toEqual({ ids: ['a', 'b'], none: false })
    expect(projectPickerLabel(all.projects, all.selectedProjectIds)).toBe('All Projects')
  })

  it('unticking the last project leaves none, not all', () => {
    const one = toggleProjectSelection(state(['a']), 'a')
    expect(one.selectedProjectIds).toEqual([])
    expect(toggleProjectSelection(one, 'b').selectedProjectIds).toEqual(['b'])
  })
})

describe('projectQuery', () => {
  it('queries nothing and shows no empty state while the list loads', () => {
    expect(projectQuery([], true)).toEqual({ ids: null, none: false })
  })

  it('treats an empty selection as none, never all', () => {
    expect(projectQuery([], false)).toEqual({ ids: null, none: true })
  })

  it('queries exactly the selected projects, keeping the array so effects do not refire', () => {
    const selected = ['a', 'b']
    expect(projectQuery(selected, false).ids).toBe(selected)
    expect(projectQuery(selected, false).none).toBe(false)
  })
})

describe('projectPickerLabel', () => {
  it('says no project is selected when nothing is ticked', () => {
    expect(projectPickerLabel([A, B], [])).toBe('No project selected')
  })

  it('says all projects only when every box is ticked', () => {
    expect(projectPickerLabel([A, B], ['b', 'a'])).toBe('All Projects')
  })

  it('names a single project, and counts several', () => {
    expect(projectPickerLabel([A, B, C], ['b'])).toBe('Bravo')
    expect(projectPickerLabel([A, B, C], ['a', 'c'])).toBe('2 Projects')
  })
})
