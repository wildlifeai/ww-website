import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { PAGE_SIZE } from './liveObservations'
import {
  deploymentPickerGroups, fetchDeploymentsInProjects, filterPickerGroups, optionCount, pickerLabels,
  UPLOAD_DEPLOYMENT_COLUMNS, type UploadTarget,
} from './uploadTargets'

type Call = [string, ...unknown[]]
type Page = { data: unknown[] | null; error: { message: string } | null }

/** A client that records each query's calls and answers the pages in turn. */
function fakeDb(pages: Page[]) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'is', 'order']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => {
        calls.push(['range', ...args])
        return Promise.resolve(pages[queries.length - 1])
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const deps = (n: number, project = 'p1', offset = 0): UploadTarget[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `d${offset + i}`, project_id: project, name: null, location_name: `Site ${offset + i}`, deployment_start: null,
  }))

describe('fetchDeploymentsInProjects', () => {
  it('reads the live deployments of the given projects', async () => {
    const { db, queries } = fakeDb([{ data: deps(2), error: null }])
    const r = await fetchDeploymentsInProjects(db, ['p1', 'p2'])
    expect(r).toEqual({ data: deps(2), error: null })
    expect(queries[0]).toEqual([
      ['from', 'deployments'],
      ['select', UPLOAD_DEPLOYMENT_COLUMNS],
      ['in', 'project_id', ['p1', 'p2']],
      ['is', 'deleted_at', null],
      ['order', 'id'],
      ['range', 0, PAGE_SIZE - 1],
    ])
  })

  it('reads past the 1,000-row cap until a short page', async () => {
    const { db, queries } = fakeDb([
      { data: deps(PAGE_SIZE), error: null },
      { data: deps(3, 'p1', PAGE_SIZE), error: null },
    ])
    const r = await fetchDeploymentsInProjects(db, ['p1'])
    expect(r.data).toHaveLength(PAGE_SIZE + 3)
    expect(queries[1].at(-1)).toEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
  })

  it('splits a long project list into chunks of 100', async () => {
    const ids = Array.from({ length: 150 }, (_, i) => `p${i}`)
    const { db, queries } = fakeDb([{ data: [], error: null }, { data: [], error: null }])
    await fetchDeploymentsInProjects(db, ids)
    expect(queries).toHaveLength(2)
    expect((queries[0][2][2] as string[]).length).toBe(100)
    expect((queries[1][2][2] as string[]).length).toBe(50)
  })

  it('returns the error instead of a partial list', async () => {
    const { db } = fakeDb([{ data: deps(PAGE_SIZE), error: null }, { data: null, error: { message: 'boom' } }])
    expect(await fetchDeploymentsInProjects(db, ['p1'])).toEqual({ data: null, error: { message: 'boom' } })
  })

  it('reads nothing for no projects', async () => {
    const { db, queries } = fakeDb([])
    expect(await fetchDeploymentsInProjects(db, [])).toEqual({ data: [], error: null })
    expect(queries).toHaveLength(0)
  })
})

describe('deploymentPickerGroups', () => {
  const projects = [{ id: 'p1', name: 'Zealandia' }, { id: 'p2', name: 'Orokonui' }]

  it('groups by project, projects by name, labelled like the grid filter', () => {
    const groups = deploymentPickerGroups([
      { id: 'aaaa1111-x', project_id: 'p1', name: null, location_name: 'Hut', deployment_start: '2026-06-09T00:00:00Z', timezone: 'UTC' },
      { id: 'bbbb2222-x', project_id: 'p2', name: 'Named only', location_name: null, deployment_start: null },
      { id: 'cccc3333-x', project_id: 'p1', name: null, location_name: 'Hut', deployment_start: '2026-06-09T00:00:00Z', timezone: 'UTC' },
    ], projects)
    expect(groups.map((g) => g.projectName)).toEqual(['Orokonui', 'Zealandia'])
    expect(groups[0].options).toEqual([{ value: 'bbbb2222-x', label: 'Named only' }])
    // Same site and date in one project: the id prefix tells them apart.
    expect(groups[1].options.map((o) => o.label)).toEqual(['Hut · 9 Jun 2026 · aaaa1111', 'Hut · 9 Jun 2026 · cccc3333'])
    expect(optionCount(groups)).toBe(3)
  })

  it('adds the project to a label only when the picker spans several', () => {
    const one = deploymentPickerGroups(deps(1, 'p1'), projects)
    expect(pickerLabels(one).get('d0')).toBe('Site 0')
    const two = deploymentPickerGroups([...deps(1, 'p1'), ...deps(1, 'p2', 1)], projects)
    expect(pickerLabels(two).get('d0')).toBe('Site 0 · Zealandia')
  })
})

describe('filterPickerGroups', () => {
  const groups = deploymentPickerGroups([...deps(3, 'p1'), ...deps(2, 'p2', 3)], [
    { id: 'p1', name: 'Zealandia' }, { id: 'p2', name: 'Orokonui' },
  ])

  it('keeps every group for an empty query', () => {
    expect(filterPickerGroups(groups, '  ')).toBe(groups)
  })

  it('matches option labels and drops empty groups', () => {
    const out = filterPickerGroups(groups, 'site 4')
    expect(out.map((g) => [g.projectName, g.options.map((o) => o.value)])).toEqual([['Orokonui', ['d4']]])
  })

  it('keeps a whole project when the query names it', () => {
    const out = filterPickerGroups(groups, 'zeal')
    expect(out.map((g) => [g.projectName, g.options.length])).toEqual([['Zealandia', 3]])
  })
})
