// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Move a deployment to another project (#288) through the ww-backend RPC `move_deployment`
// (ww-backend#272). The RPC runs as the signed-in user and checks everything itself, so the
// browser calls it directly with the user's session: a backend route would only pass the call
// on, and a service-role call has no auth.uid() and is refused with 42501.
//
// The rule, from the function: project_admin on both projects (or ww_admin), the same
// organisation, and a live target that is not archived. An organisation_manager reads every
// project in the organisation but cannot move. The picker offers only targets where that rule
// can pass, and the RPC re-checks it. Photos, observations, annotations and alerts follow the
// deployment, since they read their project through it.
//
// Each function that queries takes the Supabase client, as lib/projectMembers.ts does.
import type { SupabaseClient } from '@supabase/supabase-js'

export interface MoveResult {
  deployment_id: string
  from_project_id: string
  to_project_id: string
  moved_at: string | null
  /** false when the deployment was already in that project; nothing was written. */
  moved: boolean
}

export interface ProjectOption {
  id: string
  name: string
  organisation_id: string
  is_archived: boolean
}

/** One of the user's own `user_roles` rows, which RLS lets them read. */
export interface OwnRole {
  role: string
  scope_type: string
  scope_id: string | null
  is_active: boolean
  expires_at: string | null
}

/** What `has_project_role(..., 'project_admin')` grants the user. */
export interface AdminScope {
  wwAdmin: boolean
  adminProjectIds: Set<string>
}

export interface MoveContext {
  deploymentId: string
  locationName: string | null
  source: { id: string; name: string; organisation_id: string }
  /** Admin of the deployment's current project, the first half of the rule. */
  canMoveFrom: boolean
  targets: ProjectOption[]
}

export type MoveErrorKind =
  | 'not_allowed'
  | 'signed_out'
  | 'deployment_gone'
  | 'target_gone'
  | 'not_found'
  | 'other_organisation'
  | 'target_archived'
  | 'missing_argument'
  | 'unknown'

const MESSAGES: Record<MoveErrorKind, string> = {
  not_allowed:        'Moving a deployment needs admin rights on both its current project and the project it moves to.',
  signed_out:         'Your session has ended. Sign in again, then retry.',
  deployment_gone:    'This deployment no longer exists, or you can no longer see it.',
  target_gone:        'That project no longer exists, or you can no longer see it.',
  not_found:          'The deployment or the project no longer exists, or you can no longer see it.',
  other_organisation: 'A deployment can only move to a project in the same organisation.',
  target_archived:    'That project is archived, so nothing can move into it.',
  missing_argument:   'Choose a project to move the deployment to.',
  unknown:            'Something went wrong. Please try again.',
}

export class MoveDeploymentError extends Error {
  readonly kind: MoveErrorKind
  constructor(kind: MoveErrorKind, detail?: string) {
    super(kind === 'unknown' && detail ? detail : MESSAGES[kind])
    this.kind = kind
  }
}

interface PgError { code?: string; message?: string }

/** Map a PostgREST error from `move_deployment` to what the user can act on, by SQLSTATE. */
export function toMoveError(err: PgError): MoveDeploymentError {
  const message = err.message ?? ''
  switch (err.code) {
    case '42501':
      return new MoveDeploymentError(/not authenticated/i.test(message) ? 'signed_out' : 'not_allowed')
    case 'P0002':
      if (/target project/i.test(message)) return new MoveDeploymentError('target_gone')
      if (/deployment/i.test(message)) return new MoveDeploymentError('deployment_gone')
      return new MoveDeploymentError('not_found')
    case '22023':
      if (/archived/i.test(message)) return new MoveDeploymentError('target_archived')
      if (/organisation/i.test(message)) return new MoveDeploymentError('other_organisation')
      break
    case '22004':
      return new MoveDeploymentError('missing_argument')
  }
  return new MoveDeploymentError('unknown', message || undefined)
}

/** A role counts while active and unexpired, as `has_project_role` reads it. */
function live(r: OwnRole, now: Date): boolean {
  return r.is_active && (r.expires_at == null || new Date(r.expires_at) > now)
}

export function adminScope(roles: OwnRole[], now: Date = new Date()): AdminScope {
  const adminProjectIds = new Set<string>()
  let wwAdmin = false
  for (const r of roles) {
    if (!live(r, now)) continue
    if (r.scope_type === 'system' && r.role === 'ww_admin') wwAdmin = true
    if (r.scope_type === 'project' && r.role === 'project_admin' && r.scope_id) adminProjectIds.add(r.scope_id)
  }
  return { wwAdmin, adminProjectIds }
}

export function isProjectAdmin(scope: AdminScope, projectId: string): boolean {
  return scope.wwAdmin || scope.adminProjectIds.has(projectId)
}

/**
 * The projects the deployment can move into: another live project in the same organisation,
 * not archived, that the user administers. Sorted by name. `projects` is what the user can read.
 */
export function moveTargets(
  projects: ProjectOption[],
  scope: AdminScope,
  source: { id: string; organisation_id: string },
): ProjectOption[] {
  return projects
    .filter(p => p.id !== source.id
      && p.organisation_id === source.organisation_id
      && !p.is_archived
      && isProjectAdmin(scope, p.id))
    .sort((a, b) => a.name.localeCompare(b.name))
}

type Embedded<T> = T | T[] | null

function one<T>(v: Embedded<T>): T | null {
  return Array.isArray(v) ? (v[0] ?? null) : v
}

/** Everything the dialog needs, read as the user; null when they cannot see the deployment. */
export async function fetchMoveContext(db: SupabaseClient, userId: string, deploymentId: string): Promise<MoveContext | null> {
  const dep = await db
    .from('deployments')
    .select('id, location_name, projects(id, name, organisation_id)')
    .eq('id', deploymentId)
    .is('deleted_at', null)
    .maybeSingle()
  if (dep.error) throw new Error(dep.error.message)
  const row = dep.data as { id: string; location_name: string | null; projects: Embedded<MoveContext['source']> } | null
  const source = row ? one(row.projects) : null
  if (!row || !source) return null

  const [roles, projects] = await Promise.all([
    db.from('user_roles').select('role, scope_type, scope_id, is_active, expires_at').eq('user_id', userId),
    db.from('projects').select('id, name, organisation_id, is_archived')
      .eq('organisation_id', source.organisation_id).is('deleted_at', null),
  ])
  if (roles.error) throw new Error(roles.error.message)
  if (projects.error) throw new Error(projects.error.message)

  const scope = adminScope((roles.data ?? []) as OwnRole[])
  return {
    deploymentId: row.id,
    locationName: row.location_name,
    source,
    canMoveFrom: isProjectAdmin(scope, source.id),
    targets: moveTargets((projects.data ?? []) as ProjectOption[], scope, source),
  }
}

export async function moveDeployment(db: SupabaseClient, deploymentId: string, targetProjectId: string): Promise<MoveResult> {
  const { data, error } = await db.rpc('move_deployment', {
    p_deployment_id: deploymentId,
    p_target_project_id: targetProjectId,
  })
  if (error) throw toMoveError(error)
  return data as MoveResult
}
