// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The signed-in user's notification rules for one project (`notification_rules`, ww-backend).
// RLS lets a user read and write only their own rules, and writing needs project_member on the
// project, so a viewer's save is refused with 42501. A save asks for the row back and treats
// none as a refusal, so the panel never shows "Saved" for a change the database did not take.
import type { SupabaseClient } from '@supabase/supabase-js'

export type Channel = 'web' | 'email' | 'push'

export interface Rule {
  project_id: string
  event_type: string
  species_filter: string | null
  channels: string[]
  digest: string
  is_active: boolean
}

export const RULE_COLUMNS = 'project_id, event_type, species_filter, channels, digest, is_active'

export const NOT_ALLOWED = 'You need the Project Member role to set notifications for this project.'

/** The stored rule, or the default (no channels) when the user has none for this event. */
export function ruleFor(rules: Rule[], projectId: string, eventType: string): Rule {
  return rules.find(r => r.project_id === projectId && r.event_type === eventType)
    ?? { project_id: projectId, event_type: eventType, species_filter: null, channels: [], digest: 'immediate', is_active: false }
}

export function toggleChannel(channels: string[], channel: Channel): string[] {
  return channels.includes(channel) ? channels.filter(c => c !== channel) : [...channels, channel]
}

export async function fetchRules(db: SupabaseClient, projectId: string): Promise<Rule[]> {
  const { data, error } = await db.from('notification_rules').select(RULE_COLUMNS).eq('project_id', projectId)
  if (error) throw new Error(error.message)
  return (data ?? []) as Rule[]
}

/** Saves and returns the stored rule; throws a message the user can read when it is not saved. */
export async function saveRule(db: SupabaseClient, userId: string, rule: Rule): Promise<Rule> {
  const { data, error } = await db
    .from('notification_rules')
    .upsert(
      {
        user_id: userId,
        project_id: rule.project_id,
        event_type: rule.event_type,
        species_filter: rule.species_filter?.trim() || null,
        channels: rule.channels,
        digest: rule.digest,
        is_active: rule.channels.length > 0,
      },
      { onConflict: 'user_id,project_id,event_type' },
    )
    .select(RULE_COLUMNS)
  if (error) throw new Error(error.code === '42501' ? NOT_ALLOWED : `Not saved: ${error.message}`)
  const saved = (data as Rule[] | null)?.[0]
  if (!saved) throw new Error(NOT_ALLOWED)
  return saved
}
