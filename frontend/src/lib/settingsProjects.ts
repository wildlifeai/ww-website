// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The project list on Settings: every live project RLS lets the user read, newest first, with its
// live deployment count. The count comes from the server as deployments(count) (UI audit 05 #8)
// rather than one id per deployment. PostgREST does not refuse an embedded count with PGRST123
// while db-aggregates-enabled is off: on 26 Oct 2026 dev refused a bare count() with PGRST123 but
// passed deployments(count) on to the database, as staging did.
import type { SupabaseClient } from '@supabase/supabase-js'

export interface SettingsProject {
  id: string
  name: string
  description: string | null
  created_at: string
  deployment_count: number
}

interface RawProject {
  id: string
  name: string
  description: string | null
  created_at: string
  deployments: { count: number }[] | null
}

export function toSettingsProject(p: RawProject): SettingsProject {
  return {
    id: p.id,
    name: p.name,
    description: p.description,
    created_at: p.created_at,
    deployment_count: p.deployments?.[0]?.count ?? 0,
  }
}

export async function fetchSettingsProjects(db: SupabaseClient): Promise<SettingsProject[]> {
  const res = await db
    .from('projects')
    .select('id, name, description, created_at, deployments(count)')
    .is('deleted_at', null)
    .is('deployments.deleted_at', null)
    .order('created_at', { ascending: false })
  if (res.error) throw new Error(res.error.message)
  return ((res.data ?? []) as unknown as RawProject[]).map(toSettingsProject)
}
