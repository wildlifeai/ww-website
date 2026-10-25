import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { createProjectMessage, creationScope, fetchCreatableOrgs, type OwnRoleRow } from './projectCreation'

const now = new Date('2026-10-21T00:00:00Z')
const role = (over: Partial<OwnRoleRow>): OwnRoleRow => ({
  role: 'organisation_member', scope_type: 'organisation', scope_id: 'org-1',
  is_active: true, expires_at: null, deleted_at: null, ...over,
})

describe('creationScope', () => {
  it('counts organisation members and managers', () => {
    const scope = creationScope([
      role({ scope_id: 'general' }),
      role({ role: 'organisation_manager', scope_id: 'trust' }),
    ], now)
    expect(scope).toEqual({ wwAdmin: false, orgIds: ['general', 'trust'] })
  })

  it('ignores project roles, inactive, expired and deleted roles', () => {
    const scope = creationScope([
      role({ role: 'project_admin', scope_type: 'project', scope_id: 'p-1' }),
      role({ scope_id: 'off', is_active: false }),
      role({ scope_id: 'old', expires_at: '2026-01-01T00:00:00Z' }),
      role({ scope_id: 'gone', deleted_at: '2026-02-01T00:00:00Z' }),
    ], now)
    expect(scope).toEqual({ wwAdmin: false, orgIds: [] })
  })

  it('keeps a future expiry and flags ww_admin', () => {
    const scope = creationScope([
      role({ scope_id: 'later', expires_at: '2027-01-01T00:00:00Z' }),
      role({ role: 'ww_admin', scope_type: 'system', scope_id: null }),
    ], now)
    expect(scope).toEqual({ wwAdmin: true, orgIds: ['later'] })
  })
})

/** A client whose `user_roles` and `organisations` reads resolve to the given rows. */
function fakeDb(roles: OwnRoleRow[], orgs: { id: string; name: string }[]) {
  const calls: unknown[][] = []
  const builder = (table: string) => {
    const answer = { data: table === 'user_roles' ? roles : orgs, error: null }
    const b: Record<string, unknown> = {
      then: (resolve: (v: unknown) => unknown) => Promise.resolve(answer).then(resolve),
    }
    for (const m of ['select', 'eq', 'is', 'order', 'in']) {
      b[m] = (...args: unknown[]) => { calls.push([table, m, ...args]); return b }
    }
    return b
  }
  const from = vi.fn((table: string) => builder(table))
  return { db: { from } as unknown as SupabaseClient, calls, from }
}

describe('fetchCreatableOrgs', () => {
  it('reads nothing more when the user holds no organisation role', async () => {
    const { db, from } = fakeDb([role({ role: 'project_viewer', scope_type: 'project' })], [])
    expect(await fetchCreatableOrgs(db, 'u-1')).toEqual([])
    expect(from).toHaveBeenCalledTimes(1)
  })

  it('names the organisations the user holds a role in', async () => {
    const { db, calls } = fakeDb([role({ scope_id: 'general' })], [{ id: 'general', name: 'General' }])
    expect(await fetchCreatableOrgs(db, 'u-1')).toEqual([{ id: 'general', name: 'General' }])
    expect(calls).toContainEqual(['organisations', 'in', 'id', ['general']])
  })

  it('does not narrow the list for a ww_admin', async () => {
    const { db, calls } = fakeDb([role({ role: 'ww_admin', scope_type: 'system', scope_id: null })], [{ id: 'a', name: 'A' }])
    expect(await fetchCreatableOrgs(db, 'u-1')).toEqual([{ id: 'a', name: 'A' }])
    expect(calls.some(c => c[0] === 'organisations' && c[1] === 'in')).toBe(false)
  })
})

describe('createProjectMessage', () => {
  it('explains a refusal and passes other errors on', () => {
    expect(createProjectMessage({ code: '42501', message: 'new row violates row-level security policy' }))
      .toMatch(/cannot create projects in this organisation/)
    expect(createProjectMessage({ code: '23502', message: 'null value' })).toBe('The project was not created: null value')
  })
})
