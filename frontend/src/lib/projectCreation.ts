// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Who may create a project, and in which organisation (UI audit 04 #5). The rule is ww-backend's
// projects_insert_policy: has_organisation_role(uid, organisation, 'organisation_member'), which a
// live organisation_member or organisation_manager role passes, and a ww_admin passes everywhere.
// Every new account joins General, so most users can create a project there; a project-only
// member of another organisation cannot create one in it. Settings shows New Project only when
// this list is not empty, and the modal offers a choice when it holds more than one.
import type { SupabaseClient } from '@supabase/supabase-js'
import type { OwnRole } from './moveDeployment'

export interface OrgOption {
  id: string
  name: string
}

/** One of the user's own `user_roles` rows; RLS lets them read these. */
export type OwnRoleRow = OwnRole & { deleted_at?: string | null }

const ORG_ROLES = new Set(['organisation_member', 'organisation_manager'])

/** A role counts while active, unexpired and not deleted, as has_organisation_role reads it. */
function live(r: OwnRoleRow, now: Date): boolean {
  return r.is_active && !r.deleted_at && (r.expires_at == null || new Date(r.expires_at) > now)
}

/** The organisations the user may create a project in, from their own roles. */
export function creationScope(roles: OwnRoleRow[], now: Date = new Date()): { wwAdmin: boolean; orgIds: string[] } {
  const orgIds = new Set<string>()
  let wwAdmin = false
  for (const r of roles) {
    if (!live(r, now)) continue
    if (r.scope_type === 'system' && r.role === 'ww_admin') wwAdmin = true
    if (r.scope_type === 'organisation' && ORG_ROLES.has(r.role) && r.scope_id) orgIds.add(r.scope_id)
  }
  return { wwAdmin, orgIds: [...orgIds] }
}

/** The organisations to offer in New Project, by name; empty when the user may create none. */
export async function fetchCreatableOrgs(db: SupabaseClient, userId: string): Promise<OrgOption[]> {
  const roles = await db
    .from('user_roles')
    .select('role, scope_type, scope_id, is_active, expires_at, deleted_at')
    .eq('user_id', userId)
  if (roles.error) throw new Error(roles.error.message)
  const { wwAdmin, orgIds } = creationScope((roles.data ?? []) as OwnRoleRow[])
  if (!wwAdmin && orgIds.length === 0) return []

  // RLS shows a ww_admin every organisation, anyone else the ones they hold a role in.
  let orgs = db.from('organisations').select('id, name').is('deleted_at', null).order('name')
  if (!wwAdmin) orgs = orgs.in('id', orgIds)
  const { data, error } = await orgs
  if (error) throw new Error(error.message)
  return (data ?? []) as OrgOption[]
}

/** What to tell the user when the insert is refused. */
export function createProjectMessage(err: { code?: string; message?: string }): string {
  if (err.code === '42501') return 'You cannot create projects in this organisation. Ask its manager to add you.'
  return `The project was not created: ${err.message ?? 'unknown error'}`
}
