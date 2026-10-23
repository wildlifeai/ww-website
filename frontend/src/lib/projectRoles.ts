// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// What the signed-in user may do in a project, read from their own `user_roles` rows (RLS lets a
// user read only their own). The rules mirror ww-backend's `has_project_role`
// (supabase/schemas/public/functions/29_has_project_role.sql) exactly:
//
//   project_viewer  a project role on the project (viewer, member or admin), an
//                   organisation_manager of the project's organisation (live projects only), or
//                   ww_admin
//   project_member  project_member or project_admin on the project, or ww_admin
//   project_admin   project_admin on the project, or ww_admin
//
// A row counts while `is_active`, not soft-deleted and unexpired. An organisation_member passes
// none of these. The website backend's write check, `backend/app/authz.py` `_can_write`, is the
// same member rule. `hooks/useProjectRoles.ts` wraps this file for components.
import type { SupabaseClient } from '@supabase/supabase-js'
import type { OwnRole } from './moveDeployment'

/** One of the user's own `user_roles` rows. */
export interface RoleRow extends OwnRole {
  deleted_at: string | null
}

export const ROLE_COLUMNS = 'role, scope_type, scope_id, is_active, deleted_at, expires_at'

const PROJECT_RANK: Record<string, number> = { project_viewer: 1, project_member: 2, project_admin: 3 }

/** The user's live roles, condensed. */
export interface RoleSummary {
  systemAdmin: boolean
  /** Project id to the rank of the user's live role there (1 viewer, 2 member, 3 admin). */
  projectRank: Map<string, number>
  /** Organisations the user is a live organisation_manager of. */
  managedOrgIds: Set<string>
}

/** A row counts while active, not soft-deleted and unexpired, as `has_project_role` reads it. */
export function isLive(r: RoleRow, now: Date): boolean {
  return r.is_active && r.deleted_at == null && (r.expires_at == null || new Date(r.expires_at) > now)
}

export function summariseRoles(rows: RoleRow[], now: Date = new Date()): RoleSummary {
  const summary: RoleSummary = { systemAdmin: false, projectRank: new Map(), managedOrgIds: new Set() }
  for (const r of rows) {
    if (!isLive(r, now)) continue
    if (r.scope_type === 'system' && r.role === 'ww_admin') summary.systemAdmin = true
    if (r.scope_type === 'organisation' && r.role === 'organisation_manager' && r.scope_id) summary.managedOrgIds.add(r.scope_id)
    const rank = PROJECT_RANK[r.role]
    if (r.scope_type === 'project' && r.scope_id && rank) {
      summary.projectRank.set(r.scope_id, Math.max(rank, summary.projectRank.get(r.scope_id) ?? 0))
    }
  }
  return summary
}

/**
 * `has_project_role(project, 'project_viewer')`. `managedProjectIds` holds the live projects of
 * the organisations the user manages (`fetchManagedProjectIds`).
 */
export function canViewProject(s: RoleSummary, projectId: string, managedProjectIds: ReadonlySet<string>): boolean {
  return s.systemAdmin || s.projectRank.has(projectId) || managedProjectIds.has(projectId)
}

/** `has_project_role(project, 'project_member')`: label, upload, run AI, edit own deployments. */
export function canWriteProject(s: RoleSummary, projectId: string): boolean {
  return s.systemAdmin || (s.projectRank.get(projectId) ?? 0) >= PROJECT_RANK.project_member
}

/** `has_project_role(project, 'project_admin')`: project settings, members, delete. */
export function isAdminOfProject(s: RoleSummary, projectId: string): boolean {
  return s.systemAdmin || (s.projectRank.get(projectId) ?? 0) >= PROJECT_RANK.project_admin
}

/** The signed-in user's own role rows, live or not; `summariseRoles` decides what counts. */
export async function fetchOwnRoles(db: SupabaseClient, userId: string): Promise<RoleRow[]> {
  const { data, error } = await db.from('user_roles').select(ROLE_COLUMNS).eq('user_id', userId)
  if (error) throw new Error(error.message)
  return (data ?? []) as RoleRow[]
}

const PAGE = 1000

/** The live projects of the given organisations, which an organisation_manager may read. */
export async function fetchManagedProjectIds(db: SupabaseClient, orgIds: string[]): Promise<Set<string>> {
  const ids = new Set<string>()
  if (orgIds.length === 0) return ids
  for (let start = 0; ; start += PAGE) {
    const { data, error } = await db
      .from('projects')
      .select('id')
      .in('organisation_id', orgIds)
      .is('deleted_at', null)
      .order('id')
      .range(start, start + PAGE - 1)
    if (error) throw new Error(error.message)
    for (const row of (data ?? []) as { id: string }[]) ids.add(row.id)
    if (!data || data.length < PAGE) return ids
  }
}
