/**
 * Observation removal against a real Supabase stack: a member's delete removes
 * the row for good, and a viewer's delete, which RLS turns into "0 rows", is
 * reported as not removed instead of "Removed ✓" (#183, ww-backend#222).
 *
 * Runs only when pointed at a LOCAL, seeded stack (it creates users with the
 * service role key). From a ww-backend checkout:
 *
 *   eval "$(npx supabase status -o env | sed -n -E 's/^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=/export WW_TEST_\1=/p')"
 *   cd <ww-website>/frontend && npx vitest run src/lib/observationWrites.integration.test.ts
 *
 * Without those variables every test is skipped, so `npm test` stays offline.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { inviteMember, listMyInvitations, respondToInvitation, type ProjectRole } from './projectMembers'
import { canEditObservations, deleteObservation, NOT_REMOVED } from './observationWrites'

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

describe.skipIf(!configured)('observation removal, against a local stack', () => {
  let service: SupabaseClient
  let admin: Actor
  let member: Actor
  let viewer: Actor
  let outsider: Actor
  let projectId: string
  let deviceId: string
  let deploymentId: string

  async function signUp(role: string): Promise<Actor> {
    const email = `obs-${role}-${run}@example.test`
    const { data, error } = await service.auth.admin.createUser({
      email, password, email_confirm: true, user_metadata: { name: `${role} ${run}` },
    })
    if (error) throw error
    const db = createClient(url!, anonKey!, clientOpts)
    const { error: signInErr } = await db.auth.signInWithPassword({ email, password })
    if (signInErr) throw signInErr
    return { id: data.user.id, email, db }
  }

  async function join(actor: Actor, role: ProjectRole) {
    await inviteMember(admin.db, projectId, actor.email, role)
    const inv = (await listMyInvitations(actor.db)).find(i => i.project_id === projectId)
    await respondToInvitation(actor.db, inv!.id, true)
  }

  async function newObservation(): Promise<string> {
    const { data, error } = await service.from('observations')
      .insert({ deployment_id: deploymentId, observation_level: 'media', observation_type: 'animal', scientific_name: 'Rattus rattus' })
      .select('id').single()
    if (error) throw error
    return data.id
  }

  async function exists(obsId: string): Promise<boolean> {
    const { data, error } = await service.from('observations').select('id').eq('id', obsId)
    if (error) throw error
    return data.length === 1
  }

  beforeAll(async () => {
    service  = createClient(url!, serviceKey!, clientOpts)
    admin    = await signUp('admin')
    member   = await signUp('member')
    viewer   = await signUp('viewer')
    outsider = await signUp('outsider')

    // The project is created as the user, like CreateProjectModal, so the
    // creator becomes its admin; the device and deployment are fixtures.
    const { data: orgRole, error: orgErr } = await admin.db
      .from('user_roles').select('scope_id')
      .eq('user_id', admin.id).eq('scope_type', 'organisation').eq('is_active', true)
      .limit(1).maybeSingle()
    if (orgErr) throw orgErr
    if (!orgRole) throw new Error('New users get no organisation: the local stack is not seeded (no General organisation)')
    const { data: project, error: projErr } = await admin.db
      .from('projects')
      .insert({ name: `Observations test ${run}`, organisation_id: orgRole.scope_id, created_by: admin.id, modified_by: admin.id })
      .select('id').single()
    if (projErr) throw projErr
    projectId = project.id

    const { data: device, error: devErr } = await service.from('devices')
      .insert({ bluetooth_id: `obs-test-${run}`, name: `Observations test ${run}` }).select('id').single()
    if (devErr) throw devErr
    deviceId = device.id
    const { data: deployment, error: depErr } = await service.from('deployments')
      .insert({
        name: `Observations test ${run}`, location_name: 'Test bench', deployment_start: new Date().toISOString(),
        project_id: projectId, device_id: deviceId,
      })
      .select('id').single()
    if (depErr) throw depErr
    deploymentId = deployment.id

    await join(member, 'project_member')
    await join(viewer, 'project_viewer')
  })

  afterAll(async () => {
    if (!service) return
    if (deploymentId) await service.from('deployments').delete().eq('id', deploymentId)
    if (deviceId) await service.from('devices').delete().eq('id', deviceId)
    if (projectId) await service.from('projects').update({ deleted_at: new Date().toISOString() }).eq('id', projectId)
    for (const a of [admin, member, viewer, outsider]) if (a) await service.auth.admin.deleteUser(a.id)
  })

  it('members and admins may edit; viewers and outsiders may not', async () => {
    expect(await canEditObservations(admin.db, admin.id, deploymentId)).toBe(true)
    expect(await canEditObservations(member.db, member.id, deploymentId)).toBe(true)
    expect(await canEditObservations(viewer.db, viewer.id, deploymentId)).toBe(false)
    expect(await canEditObservations(outsider.db, outsider.id, deploymentId)).toBe(false)
  })

  it("a viewer's delete is reported as not removed, and the row stays", async () => {
    const obsId = await newObservation()
    await expect(deleteObservation(viewer.db, obsId)).rejects.toThrow(NOT_REMOVED)
    expect(await exists(obsId)).toBe(true)
  })

  it("a member's delete removes the row for good", async () => {
    const obsId = await newObservation()
    await deleteObservation(member.db, obsId)
    expect(await exists(obsId)).toBe(false)
  })

  it('nobody edits on a deleted deployment', async () => {
    await service.from('deployments').update({ deleted_at: new Date().toISOString() }).eq('id', deploymentId)
    expect(await canEditObservations(member.db, member.id, deploymentId)).toBe(false)
  })
})
