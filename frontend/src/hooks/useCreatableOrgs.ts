// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The organisations the user may create a project in (lib/projectCreation.ts). Settings reads it
// to show New Project, and the modal to offer the organisation; both share one request.
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchCreatableOrgs, type OrgOption } from '../lib/projectCreation'
import { useAuth } from './useAuth'

const NONE: OrgOption[] = []

export function useCreatableOrgs(): { orgs: OrgOption[]; loading: boolean } {
  const { user } = useAuth()
  const query = useQuery({
    queryKey: ['creatableOrgs', user?.id],
    queryFn: () => fetchCreatableOrgs(supabase, user!.id),
    enabled: !!user,
    staleTime: 60_000,
  })
  return { orgs: query.data ?? NONE, loading: query.isPending }
}
