import { describe, expect, it } from 'vitest'
import { filterProjects, groupByOrganisation, projectPickerTitle, selectedFirst } from './projectPicker'
import { toSelectableProject } from './projectSelection'

const A = { id: 'a', name: 'Alpha ridge', organisation: 'Wildlife.ai' }
const B = { id: 'b', name: 'Bravo bush', organisation: 'Auckland Council' }
const C = { id: 'c', name: 'Charlie creek', organisation: 'Wildlife.ai' }
const D = { id: 'd', name: 'Delta dunes', organisation: null }

describe('filterProjects', () => {
  it('matches the name or the organisation, ignoring case and spaces around the query', () => {
    expect(filterProjects([A, B, C], '  RIDGE ')).toEqual([A])
    expect(filterProjects([A, B, C], 'council')).toEqual([B])
  })

  it('returns every project for an empty query', () => {
    expect(filterProjects([A, B], '')).toEqual([A, B])
  })
})

describe('groupByOrganisation', () => {
  it('keeps one unlabelled group when every project is in one organisation', () => {
    expect(groupByOrganisation([A, C])).toEqual([{ organisation: null, projects: [A, C] }])
  })

  it('groups by organisation name with the unreadable organisation last', () => {
    expect(groupByOrganisation([A, B, C, D])).toEqual([
      { organisation: 'Auckland Council', projects: [B] },
      { organisation: 'Wildlife.ai', projects: [A, C] },
      { organisation: null, projects: [D] },
    ])
  })
})

describe('projectPickerTitle', () => {
  it('names the ticked projects', () => {
    expect(projectPickerTitle([A, B, C], ['a', 'c'])).toBe('Alpha ridge\nCharlie creek')
  })

  it('cuts a long selection', () => {
    expect(projectPickerTitle([A, B, C, D], ['a', 'b', 'c', 'd'], 2)).toBe('Alpha ridge\nBravo bush\nand 2 more')
  })

  it('says so when nothing is ticked', () => {
    expect(projectPickerTitle([A], [])).toBe('No project selected')
  })
})

describe('selectedFirst', () => {
  it('puts the selected projects first and keeps the order within each part', () => {
    expect(selectedFirst([A, B, C, D], ['d', 'b']).map(p => p.id)).toEqual(['b', 'd', 'a', 'c'])
  })
})

describe('toSelectableProject', () => {
  it('reads the organisation embed as an object or a one-row array', () => {
    expect(toSelectableProject({ id: 'a', name: 'A', organisations: { name: 'Org' } }).organisation).toBe('Org')
    expect(toSelectableProject({ id: 'a', name: 'A', organisations: [{ name: 'Org' }] }).organisation).toBe('Org')
  })

  it('leaves the organisation null when RLS hides it', () => {
    expect(toSelectableProject({ id: 'a', name: 'A', organisations: null })).toEqual({
      id: 'a', name: 'A', organisation: null, latestDeploymentStart: null, createdAt: null,
    })
  })
})
