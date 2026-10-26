// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The project list on Settings: every live project RLS lets the user read, newest first, with its
// live deployment count. The count comes from the server as deployments(count) (UI audit 05 #8)
// rather than one id per deployment. PostgREST refuses aggregates with PGRST123 when
// db-aggregates-enabled is off; the embedded count is meant to be exempt, but until that is seen
// on every environment a refusal falls back to reading the ids.
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
  deployments: { count?: number; id?: string }[] | null
}

export const AGGREGATES_DISABLED = 'PGRST123'

function read(db: SupabaseClient, deployments: 'count' | 'id') {
  return db
    .from('projects')
    .select(`id, name, description, created_at, deployments(${deployments})`)
    .is('deleted_at', null)
    .is('deployments.deleted_at', null)
    .order('created_at', { ascending: false })
}

export function toSettingsProject(p: RawProject): SettingsProject {
  const deps = p.deployments ?? []
  const counted = deps.length === 1 && typeof deps[0].count === 'number'
  return {
    id: p.id,
    name: p.name,
    description: p.description,
    created_at: p.created_at,
    deployment_count: counted ? deps[0].count! : deps.length,
  }
}

export async function fetchSettingsProjects(db: SupabaseClient): Promise<SettingsProject[]> {
  let res = await read(db, 'count')
  if (res.error?.code === AGGREGATES_DISABLED) res = await read(db, 'id')
  if (res.error) throw new Error(res.error.message)
  return ((res.data ?? []) as unknown as RawProject[]).map(toSettingsProject)
}
