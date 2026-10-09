import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  adminScope, fetchMoveContext, isProjectAdmin, moveDeployment, moveTargets, toMoveError,
  type OwnRole, type ProjectOption,
} from './moveDeployment'

const NOW = new Date('2026-10-09T00:00:00Z')

const role = (patch: Partial<OwnRole>): OwnRole => ({
  role: 'project_admin', scope_type: 'project', scope_id: 'p-a', is_active: true, expires_at: null, ...patch,
})

const project = (id: string, patch: Partial<ProjectOption> = {}): ProjectOption => ({
  id, name: id.toUpperCase(), organisation_id: 'org-1', is_archived: false, ...patch,
})

describe('toMoveError', () => {
  it.each([
    [{ code: '42501', message: 'Permission denied: moving a deployment needs project_admin on both projects' }, 'not_allowed'],
    [{ code: '42501', message: 'Not authenticated' }, 'signed_out'],
    [{ code: '42501', message: 'new row violates row-level security policy' }, 'not_allowed'],
    [{ code: 'P0002', message: 'Deployment not found' }, 'deployment_gone'],
    [{ code: 'P0002', message: 'Target project not found' }, 'target_gone'],
    [{ code: 'P0002', message: '' }, 'not_found'],
    [{ code: '22023', message: 'A deployment cannot move to a project in another organisation' }, 'other_organisation'],
    [{ code: '22023', message: 'A deployment cannot move to an archived project' }, 'target_archived'],
    [{ code: '22023', message: 'something else' }, 'unknown'],
    [{ code: '22004', message: 'Parameter p_target_project_id cannot be null' }, 'missing_argument'],
    [{ code: 'PGRST202', message: 'Could not find the function public.move_deployment' }, 'unknown'],
  ])('maps %o to %s', (err, kind) => {
    expect(toMoveError(err).kind).toBe(kind)
  })

  it('gives each refusal a plain message, and keeps the server text for one it does not know', () => {
    expect(toMoveError({ code: '42501', message: 'x' }).message).toMatch(/admin rights on both/)
    expect(toMoveError({ code: '22023', message: 'A deployment cannot move to an archived project' }).message).toMatch(/archived/)
    expect(toMoveError({ code: 'XX000', message: 'boom' }).message).toBe('boom')
    expect(toMoveError({}).message).toBe('Something went wrong. Please try again.')
  })
})

describe('adminScope', () => {
  it('collects live project_admin roles and ww_admin, as has_project_role reads them', () => {
    const scope = adminScope([
      role({ scope_id: 'p-a' }),
      role({ scope_id: 'p-b', role: 'project_member' }),
      role({ scope_id: 'p-c', is_active: false }),
      role({ scope_id: 'p-d', expires_at: '2026-10-01T00:00:00Z' }),
      role({ scope_id: 'p-e', expires_at: '2027-01-01T00:00:00Z' }),
      role({ role: 'organisation_manager', scope_type: 'organisation', scope_id: 'org-1' }),
    ], NOW)
    expect([...scope.adminProjectIds].sort()).toEqual(['p-a', 'p-e'])
    expect(scope.wwAdmin).toBe(false)
  })

  it('treats a live ww_admin as admin of every project, and an expired one as nothing', () => {
    const live = adminScope([role({ role: 'ww_admin', scope_type: 'system', scope_id: null })], NOW)
    expect(isProjectAdmin(live, 'anything')).toBe(true)
    const expired = adminScope([role({ role: 'ww_admin', scope_type: 'system', scope_id: null, expires_at: '2026-01-01T00:00:00Z' })], NOW)
    expect(isProjectAdmin(expired, 'anything')).toBe(false)
  })
})

describe('moveTargets', () => {
  const source = { id: 'p-a', organisation_id: 'org-1' }
  const projects = [
    project('p-a'),
    project('p-z', { name: 'Zealandia' }),
    project('p-b', { name: 'Brook' }),
    project('p-x', { organisation_id: 'org-2' }),
    project('p-r', { is_archived: true }),
    project('p-m'),
  ]

  it('offers other live projects in the same organisation that the user administers, by name', () => {
    const scope = adminScope([role({ scope_id: 'p-a' }), role({ scope_id: 'p-b' }), role({ scope_id: 'p-z' }), role({ scope_id: 'p-x' }), role({ scope_id: 'p-r' })], NOW)
    expect(moveTargets(projects, scope, source).map(p => p.id)).toEqual(['p-b', 'p-z'])
  })

  it('gives a ww_admin every eligible project in the organisation', () => {
    const scope = adminScope([role({ role: 'ww_admin', scope_type: 'system', scope_id: null })], NOW)
    expect(moveTargets(projects, scope, source).map(p => p.id)).toEqual(['p-b', 'p-m', 'p-z'])
  })
})

/** A query builder that records its filters and resolves to the canned answer for its table. */
function fakeDb(tables: Record<string, { data: unknown; error: unknown }>, rpcResult = { data: null as unknown, error: null as unknown }) {
  const calls: { table: string; filters: unknown[][] }[] = []
  const from = vi.fn((table: string) => {
    const entry = { table, filters: [] as unknown[][] }
    calls.push(entry)
    const answer = tables[table] ?? { data: null, error: null }
    const builder: Record<string, unknown> = {
      then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => Promise.resolve(answer).then(resolve, reject),
    }
    for (const m of ['select', 'eq', 'is']) {
      builder[m] = (...args: unknown[]) => { entry.filters.push([m, ...args]); return builder }
    }
    builder.maybeSingle = () => Promise.resolve(answer)
    return builder
  })
  const rpc = vi.fn().mockResolvedValue(rpcResult)
  return { db: { from, rpc } as unknown as SupabaseClient, calls, rpc }
}

describe('fetchMoveContext', () => {
  const deployment = { id: 'dep-1', location_name: 'Ridge track', projects: { id: 'p-a', name: 'Alpha', organisation_id: 'org-1' } }

  it('reads the deployment, the user own roles and the organisation projects, and works out the targets', async () => {
    const { db, calls } = fakeDb({
      deployments: { data: deployment, error: null },
      user_roles: { data: [role({ scope_id: 'p-a' }), role({ scope_id: 'p-b' })], error: null },
      projects: { data: [project('p-a'), project('p-b', { name: 'Brook' }), project('p-c')], error: null },
    })
    const ctx = await fetchMoveContext(db, 'user-1', 'dep-1')
    expect(ctx).toMatchObject({
      deploymentId: 'dep-1', locationName: 'Ridge track', canMoveFrom: true,
      source: { id: 'p-a', name: 'Alpha', organisation_id: 'org-1' },
    })
    expect(ctx?.targets.map(t => t.id)).toEqual(['p-b'])
    expect(calls.find(c => c.table === 'user_roles')?.filters).toContainEqual(['eq', 'user_id', 'user-1'])
    expect(calls.find(c => c.table === 'projects')?.filters).toContainEqual(['eq', 'organisation_id', 'org-1'])
    expect(calls.find(c => c.table === 'deployments')?.filters).toContainEqual(['is', 'deleted_at', null])
  })

  it('says the user cannot move out of a project they do not administer', async () => {
    const { db } = fakeDb({
      deployments: { data: { ...deployment, projects: [deployment.projects] }, error: null },
      user_roles: { data: [role({ scope_id: 'p-b' })], error: null },
      projects: { data: [project('p-a'), project('p-b')], error: null },
    })
    const ctx = await fetchMoveContext(db, 'user-1', 'dep-1')
    expect(ctx?.canMoveFrom).toBe(false)
  })

  it('is null for a deployment the user cannot see, and throws a read error', async () => {
    expect(await fetchMoveContext(fakeDb({ deployments: { data: null, error: null } }).db, 'u', 'd')).toBeNull()
    await expect(fetchMoveContext(fakeDb({ deployments: { data: null, error: { message: 'down' } } }).db, 'u', 'd')).rejects.toThrow('down')
  })
})

describe('moveDeployment', () => {
  it('calls the RPC with the contract argument names and returns its result', async () => {
    const result = { deployment_id: 'dep-1', from_project_id: 'p-a', to_project_id: 'p-b', moved_at: '2026-10-09T01:00:00Z', moved: true }
    const { db, rpc } = fakeDb({}, { data: result, error: null })
    await expect(moveDeployment(db, 'dep-1', 'p-b')).resolves.toEqual(result)
    expect(rpc).toHaveBeenCalledWith('move_deployment', { p_deployment_id: 'dep-1', p_target_project_id: 'p-b' })
  })

  it('throws the mapped error when the RPC refuses', async () => {
    const { db } = fakeDb({}, { data: null, error: { code: '22023', message: 'A deployment cannot move to an archived project' } })
    await expect(moveDeployment(db, 'dep-1', 'p-b')).rejects.toMatchObject({ kind: 'target_archived' })
  })
})
