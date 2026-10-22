// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useProjectRoles: what the signed-in user may do in each project (UI audit decision 1,
// documentation/development reports/2026-10_ui-audit/README.md). Pages hide what the role cannot
// do, so nothing is shown and then refused.
//
// The rules mirror ww-backend's has_project_role and live in lib/projectRoles.ts. One React Query
// key per user, so every component shares one read of the user's own user_roles rows; a second
// read, only for an organisation_manager, lists the live projects of the organisations they
// manage. Both refetch when the user returns to the tab, at most every 30 s.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { useAuth } from './useAuth'
import {
  canViewProject, canWriteProject, fetchManagedProjectIds, fetchOwnRoles, isAdminOfProject, summariseRoles,
} from '../lib/projectRoles'
import { TAB_RETURN_REFRESH_MS } from '../lib/tabReturnRefresh'

export interface ProjectRoles {
  /** True while the user's roles are loading: render nothing role-dependent yet. */
  loading: boolean
  /** ww_admin. */
  isSystemAdmin: boolean
  /** The user is organisation_manager of at least one organisation. */
  managesAnyOrg: boolean
  /** organisation_manager of this organisation. */
  isOrgManager: (orgId: string) => boolean
  /** has_project_role(project, 'project_viewer'): may read the project. */
  canView: (projectId: string) => boolean
  /** has_project_role(project, 'project_member'): may label, upload, run AI, edit own deployments. */
  canWrite: (projectId: string) => boolean
  /** has_project_role(project, 'project_admin'): may change project settings, members, delete. */
  isProjectAdmin: (projectId: string) => boolean
}

/** The query key prefix; invalidate it after a change to the user's own roles. */
export const PROJECT_ROLES_KEY = 'projectRoles'

const NO_PROJECTS: ReadonlySet<string> = new Set()

export function useProjectRoles(): ProjectRoles {
  const { user, loading: authLoading } = useAuth()
  const userId = user?.id

  const roles = useQuery({
    queryKey: [PROJECT_ROLES_KEY, userId],
    queryFn: () => fetchOwnRoles(supabase, userId as string),
    enabled: !!userId,
    staleTime: TAB_RETURN_REFRESH_MS,
    refetchOnWindowFocus: true,
  })
  const summary = useMemo(() => summariseRoles(roles.data ?? []), [roles.data])

  const managedOrgIds = useMemo(() => [...summary.managedOrgIds].toSorted(), [summary])
  const managedProjects = useQuery({
    queryKey: [PROJECT_ROLES_KEY, userId, 'managedProjects', managedOrgIds],
    queryFn: () => fetchManagedProjectIds(supabase, managedOrgIds),
    enabled: !!userId && managedOrgIds.length > 0,
    staleTime: TAB_RETURN_REFRESH_MS,
    refetchOnWindowFocus: true,
  })

  return useMemo(() => {
    const managed = managedProjects.data ?? NO_PROJECTS
    const loading = authLoading
      || (!!userId && roles.isPending)
      || (managedOrgIds.length > 0 && managedProjects.isPending)
    return {
      loading,
      isSystemAdmin: summary.systemAdmin,
      managesAnyOrg: summary.managedOrgIds.size > 0,
      isOrgManager: (orgId: string) => summary.managedOrgIds.has(orgId),
      canView: (projectId: string) => canViewProject(summary, projectId, managed),
      canWrite: (projectId: string) => canWriteProject(summary, projectId),
      isProjectAdmin: (projectId: string) => isAdminOfProject(summary, projectId),
    }
  }, [authLoading, userId, roles.isPending, summary, managedOrgIds, managedProjects.isPending, managedProjects.data])
}
