import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  canViewProject, canWriteProject, fetchManagedProjectIds, fetchOwnRoles, isAdminOfProject, isLive,
  ROLE_COLUMNS, summariseRoles, type RoleRow,
} from './projectRoles'

const NOW = new Date('2026-10-21T00:00:00Z')
const NONE: ReadonlySet<string> = new Set()

function role(r: Partial<RoleRow>): RoleRow {
  return { role: 'project_member', scope_type: 'project', scope_id: 'p1', is_active: true, deleted_at: null, expires_at: null, ...r }
}

/** has_project_role's three levels for one user and one project, as this lib answers them. */
function levels(rows: RoleRow[], projectId = 'p1', managed: ReadonlySet<string> = NONE) {
  const s = summariseRoles(rows, NOW)
  return [canViewProject(s, projectId, managed), canWriteProject(s, projectId), isAdminOfProject(s, projectId)]
}

describe('has_project_role, mirrored', () => {
  it.each([
    ['project_viewer', [true, false, false]],
    ['project_member', [true, true, false]],
    ['project_admin', [true, true, true]],
  ])('a %s on the project', (name, expected) => {
    expect(levels([role({ role: name })])).toEqual(expected)
  })

  it('gives ww_admin every level on every project', () => {
    expect(levels([role({ role: 'ww_admin', scope_type: 'system', scope_id: null })], 'any')).toEqual([true, true, true])
  })

  it('lets an organisation_manager read the live projects of their organisation, and nothing more', () => {
    const rows = [role({ role: 'organisation_manager', scope_type: 'organisation', scope_id: 'o1' })]
    expect(levels(rows, 'p1', new Set(['p1']))).toEqual([true, false, false])
    expect(levels(rows, 'p2', new Set(['p1']))).toEqual([false, false, false])
  })

  it('gives an organisation_member nothing at project level', () => {
    expect(levels([role({ role: 'organisation_member', scope_type: 'organisation', scope_id: 'o1' })])).toEqual([false, false, false])
  })

  it('keeps a project role to its own project', () => {
    expect(levels([role({ role: 'project_admin' })], 'p2')).toEqual([false, false, false])
  })

  it('ignores a role that is inactive, soft-deleted or expired', () => {
    for (const dead of [{ is_active: false }, { deleted_at: '2026-10-01T00:00:00Z' }, { expires_at: '2026-10-20T00:00:00Z' }]) {
      expect(levels([role({ role: 'project_admin', ...dead })])).toEqual([false, false, false])
      expect(summariseRoles([role({ role: 'ww_admin', scope_type: 'system', scope_id: null, ...dead })], NOW).systemAdmin).toBe(false)
    }
    expect(levels([role({ role: 'project_admin', expires_at: '2026-10-22T00:00:00Z' })])).toEqual([true, true, true])
  })

  it('takes the highest live role when old rows remain beside the current one', () => {
    const rows = [role({ role: 'project_admin', deleted_at: '2026-01-01T00:00:00Z' }), role({ role: 'project_viewer' })]
    expect(levels(rows)).toEqual([true, false, false])
  })
})

describe('summariseRoles', () => {
  it('collects the organisations the user manages, live only', () => {
    const s = summariseRoles([
      role({ role: 'organisation_manager', scope_type: 'organisation', scope_id: 'o1' }),
      role({ role: 'organisation_manager', scope_type: 'organisation', scope_id: 'o2', is_active: false }),
      role({ role: 'organisation_member', scope_type: 'organisation', scope_id: 'o3' }),
    ], NOW)
    expect([...s.managedOrgIds]).toEqual(['o1'])
  })

  it('reads expiry against the given time', () => {
    expect(isLive(role({ expires_at: '2026-10-21T00:00:01Z' }), NOW)).toBe(true)
    expect(isLive(role({ expires_at: '2026-10-21T00:00:00Z' }), NOW)).toBe(false)
  })
})

/** A client whose query chain records calls and resolves to the next queued result. */
function fakeDb(results: { data: unknown; error: { message: string } | null }[]) {
  const calls: [string, unknown[]][] = []
  const chain: Record<string, unknown> = {}
  for (const m of ['from', 'select', 'eq', 'in', 'is', 'order', 'range']) {
    chain[m] = vi.fn((...args: unknown[]) => { calls.push([m, args]); return chain })
  }
  chain.then = (resolve: (r: unknown) => unknown) => Promise.resolve(results.shift()).then(resolve)
  return { db: chain as unknown as SupabaseClient, calls }
}

describe('fetchOwnRoles', () => {
  it('reads the user’s own rows with the columns the rules need', async () => {
    const { db, calls } = fakeDb([{ data: [role({})], error: null }])
    expect(await fetchOwnRoles(db, 'u1')).toHaveLength(1)
    expect(calls).toContainEqual(['select', [ROLE_COLUMNS]])
    expect(calls).toContainEqual(['eq', ['user_id', 'u1']])
  })

  it('passes a database error through', async () => {
    const { db } = fakeDb([{ data: null, error: { message: 'boom' } }])
    await expect(fetchOwnRoles(db, 'u1')).rejects.toThrow('boom')
  })
})

describe('fetchManagedProjectIds', () => {
  it('pages past 1,000 rows and skips deleted projects', async () => {
    const page = Array.from({ length: 1000 }, (_, i) => ({ id: `p${i}` }))
    const { db, calls } = fakeDb([{ data: page, error: null }, { data: [{ id: 'last' }], error: null }])
    const ids = await fetchManagedProjectIds(db, ['o1'])
    expect(ids.size).toBe(1001)
    expect(calls).toContainEqual(['is', ['deleted_at', null]])
    expect(calls).toContainEqual(['range', [1000, 1999]])
  })

  it('asks nothing when the user manages no organisation', async () => {
    const { db, calls } = fakeDb([])
    expect((await fetchManagedProjectIds(db, [])).size).toBe(0)
    expect(calls).toHaveLength(0)
  })
})
