// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Whether the signed-in user may create a project: ww-backend's projects INSERT policy allows an
// organisation role (organisation_member or organisation_manager, the only roles at that scope),
// so a project-only member or viewer cannot (UI audit, "settled by the code"). Read only where a
// "Create a project" path shows, the empty states for a user with no project.
//
// useProjectRoles has no organisation-member answer yet; when it gains one, this hook folds into it.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from './useAuth'
import { useProjectRoles } from './useProjectRoles'
import { supabase } from '../config/supabase'

/** null while it resolves; never true for the demo account. */
export function useCanCreateProject(enabled = true): boolean | null {
  const { user, isDemo } = useAuth()
  const { isSystemAdmin } = useProjectRoles()
  const ask = enabled && !!user && !isDemo && !isSystemAdmin

  const { data } = useQuery({
    queryKey: ['canCreateProject', user?.id],
    enabled: ask,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      // RLS shows a user their own user_roles rows.
      const { data: rows, error } = await supabase
        .from('user_roles')
        .select('id')
        .eq('user_id', user?.id ?? '')
        .eq('scope_type', 'organisation')
        .eq('is_active', true)
        .is('deleted_at', null)
        .or(`expires_at.is.null,expires_at.gt.${new Date().toISOString()}`)
        .limit(1)
      return !error && (rows?.length ?? 0) > 0
    },
  })

  if (!user || isDemo) return false
  if (isSystemAdmin) return true
  return data ?? null
}
