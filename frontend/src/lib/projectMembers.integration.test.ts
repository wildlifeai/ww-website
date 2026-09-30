/**
 * The members panel's data layer against a real Supabase stack: real RLS,
 * real RPCs, real signed-in users. This is the check that would have caught
 * the panel showing only the caller and "removing" members it had not.
 *
 * Runs only when pointed at a LOCAL stack (it creates users with the service
 * role key). The stack must be seeded, since new users join the General
 * organisation from `supabase/seeds/seed.sql`. From a ww-backend checkout:
 *
 *   eval "$(npx supabase status -o env | sed -n -E 's/^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=/export WW_TEST_\1=/p')"
 *   cd <ww-website>/frontend && npx vitest run src/lib/projectMembers.integration.test.ts
 *
 * Without those variables every test is skipped, so `npm test` stays offline.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import {
  cancelInvitation, inviteMember, listMembers, listMyInvitations, listPendingInvitations,
  removeMember, respondToInvitation, toMembersError,
} from './projectMembers'

// The app tsconfig carries browser types only; Vitest runs this file in Node.
declare const process: { env: Record<string, string | undefined> }

const url         = process.env.WW_TEST_API_URL
const anonKey     = process.env.WW_TEST_ANON_KEY
const serviceKey  = process.env.WW_TEST_SERVICE_ROLE_KEY
const configured  = !!(url && anonKey && serviceKey)

if (configured && !/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?\/?$/.test(url!)) {
  throw new Error(`WW_TEST_API_URL must be a local stack, got ${url}`)
}

const run      = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
// Generated per run for throwaway local users; nothing is stored.
const password = crypto.randomUUID()
const clientOpts = { auth: { persistSession: false, autoRefreshToken: false } }

interface Actor { id: string; email: string; db: SupabaseClient }

describe.skipIf(!configured)('project members, against a local stack', () => {
  let service: SupabaseClient
  let admin: Actor
  let invitee: Actor
  let outsider: Actor
  let projectId: string

  async function signUp(role: string): Promise<Actor> {
    // Mixed case on purpose: Auth stores it lowercased, and so must the invitation.
    const email = `Members-${role}-${run}@Example.test`
    const { data, error } = await service.auth.admin.createUser({
      email, password, email_confirm: true, user_metadata: { name: `${role} ${run}` },
    })
    if (error) throw error
    const db = createClient(url!, anonKey!, clientOpts)
    const { error: signInErr } = await db.auth.signInWithPassword({ email, password })
    if (signInErr) throw signInErr
    return { id: data.user.id, email, db }
  }

  beforeAll(async () => {
    service  = createClient(url!, serviceKey!, clientOpts)
    admin    = await signUp('admin')
    invitee  = await signUp('invitee')
    outsider = await signUp('outsider')

    // Create the project the way CreateProjectModal does: as the user, in
    // the organisation their first organisation role points at (General).
    const { data: orgRole, error: orgErr } = await admin.db
      .from('user_roles').select('scope_id')
      .eq('user_id', admin.id).eq('scope_type', 'organisation').eq('is_active', true)
      .limit(1).maybeSingle()
    if (orgErr) throw orgErr
    if (!orgRole) throw new Error('New users get no organisation: the local stack is not seeded (no General organisation)')
    const { data: project, error: projErr } = await admin.db
      .from('projects')
      .insert({ name: `Members test ${run}`, organisation_id: orgRole.scope_id, created_by: admin.id, modified_by: admin.id })
      .select('id').single()
    if (projErr) throw projErr
    projectId = project.id
  })

  afterAll(async () => {
    if (!service) return
    if (projectId) await service.from('projects').update({ deleted_at: new Date().toISOString() }).eq('id', projectId)
    for (const a of [admin, invitee, outsider]) if (a) await service.auth.admin.deleteUser(a.id)
  })

  it('the creator is listed as the only admin, with name and email', async () => {
    const members = await listMembers(admin.db, projectId)
    expect(members).toEqual([expect.objectContaining({ id: admin.id, email: admin.email.toLowerCase(), role: 'project_admin' })])
  })

  it('an outsider can neither list members nor invite', async () => {
    await expect(listMembers(outsider.db, projectId)).rejects.toMatchObject({ kind: 'not_allowed' })
    await expect(inviteMember(outsider.db, projectId, outsider.email, 'project_admin'))
      .rejects.toMatchObject({ kind: 'not_allowed' })
  })

  it('an admin invites by email and sees it pending; a repeat is refused', async () => {
    await inviteMember(admin.db, projectId, invitee.email, 'project_member')
    const pending = await listPendingInvitations(admin.db, projectId)
    expect(pending.map(p => p.invitee_email)).toEqual([invitee.email.toLowerCase()])
    await expect(inviteMember(admin.db, projectId, invitee.email.toUpperCase(), 'project_member'))
      .rejects.toMatchObject({ kind: 'already_pending' })
  })

  it('the invitee sees the invitation and accepting makes them a member', async () => {
    const mine = await listMyInvitations(invitee.db)
    const inv = mine.find(i => i.project_id === projectId)
    expect(inv).toMatchObject({ role: 'project_member' })
    expect(await listMyInvitations(outsider.db)).toEqual([])

    await respondToInvitation(invitee.db, inv!.id, true)

    const members = await listMembers(admin.db, projectId)
    expect(members.map(m => [m.id, m.role])).toEqual(
      expect.arrayContaining([[admin.id, 'project_admin'], [invitee.id, 'project_member']]),
    )
    expect(await listPendingInvitations(admin.db, projectId)).toEqual([])
    // A member can see the list too, which is what the mobile app's offline cache relies on.
    expect((await listMembers(invitee.db, projectId)).length).toBe(2)
  })

  it('a member cannot remove anyone', async () => {
    await expect(removeMember(invitee.db, projectId, admin.id, invitee.id))
      .rejects.toMatchObject({ kind: 'not_allowed' })
    expect((await listMembers(admin.db, projectId)).length).toBe(2)
  })

  it('the last admin cannot remove themselves', async () => {
    await expect(removeMember(admin.db, projectId, admin.id, admin.id))
      .rejects.toMatchObject({ kind: 'last_admin' })
  })

  it('an admin removes a member, and the member really is gone', async () => {
    await removeMember(admin.db, projectId, invitee.id, admin.id)
    const members = await listMembers(admin.db, projectId)
    expect(members.map(m => m.id)).toEqual([admin.id])
    await expect(listMembers(invitee.db, projectId)).rejects.toMatchObject({ kind: 'not_allowed' })
  })

  it('a declined invitation leaves no membership behind', async () => {
    await inviteMember(admin.db, projectId, outsider.email, 'project_member')
    const inv = (await listMyInvitations(outsider.db)).find(i => i.project_id === projectId)
    await respondToInvitation(outsider.db, inv!.id, false)
    expect((await listMembers(admin.db, projectId)).map(m => m.id)).toEqual([admin.id])
    await expect(respondToInvitation(outsider.db, inv!.id, true)).rejects.toMatchObject({ kind: 'invitation_gone' })
  })

  it('an admin cancels a pending invitation, and the invitee can no longer accept it', async () => {
    const id = await inviteMember(admin.db, projectId, outsider.email, 'project_member')
    await cancelInvitation(admin.db, id)
    expect(await listPendingInvitations(admin.db, projectId)).toEqual([])
    expect((await listMyInvitations(outsider.db)).filter(i => i.project_id === projectId)).toEqual([])
    await expect(respondToInvitation(outsider.db, id, true)).rejects.toMatchObject({ kind: 'invitation_gone' })
    await expect(cancelInvitation(admin.db, id)).rejects.toMatchObject({ kind: 'invitation_gone' })
  })

  it('only an admin can cancel', async () => {
    const id = await inviteMember(admin.db, projectId, outsider.email, 'project_member')
    await expect(cancelInvitation(outsider.db, id)).rejects.toMatchObject({ kind: 'not_allowed' })
    expect((await listPendingInvitations(admin.db, projectId)).map(p => p.id)).toEqual([id])
    await cancelInvitation(admin.db, id)
  })

  it('the server refuses an existing member whatever the casing', async () => {
    // currentMembers = [] skips the client check, so the refusal is the server's.
    await expect(inviteMember(admin.db, projectId, admin.email, 'project_member', []))
      .rejects.toMatchObject({ kind: 'already_member' })
    // inviteMember lowercases, so call the RPC directly to send a different casing.
    const { error } = await admin.db.rpc('send_project_invitation', {
      p_project_id: projectId, p_invitee_email: admin.email.toUpperCase(), p_role: 'project_member',
    })
    expect(error && toMembersError(error).kind).toBe('already_member')
  })
})
