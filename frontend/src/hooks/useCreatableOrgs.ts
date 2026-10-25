// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The organisations the user may create a project in (lib/projectCreation.ts), and so whether they
// may create one at all: ww-backend's projects INSERT policy needs an organisation role, so a
// project-only member cannot. Settings, the New Project modal and the empty states share one
// request; `enabled` lets an empty state ask only when it shows.
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchCreatableOrgs, type OrgOption } from '../lib/projectCreation'
import { useAuth } from './useAuth'

const NONE: OrgOption[] = []

export function useCreatableOrgs(enabled = true): {
  orgs: OrgOption[]
  loading: boolean
  /** null while it resolves (or is not asked); never true for the demo account. */
  canCreate: boolean | null
} {
  const { user, isDemo } = useAuth()
  const query = useQuery({
    queryKey: ['creatableOrgs', user?.id],
    queryFn: () => fetchCreatableOrgs(supabase, user!.id),
    enabled: enabled && !!user,
    staleTime: 60_000,
  })
  const orgs = query.data ?? NONE
  const canCreate = !user || isDemo ? false : query.data ? orgs.length > 0 : null
  return { orgs, loading: query.isPending, canCreate }
}
