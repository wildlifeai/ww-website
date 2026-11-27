// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The project row behind Settings → Defaults (components/settings/ProjectDefaultsPanel.tsx) and its
// save. The update runs as the signed-in user: RLS lets a project_admin of the project, or a
// ww_admin, update it, and turns anyone else's update into 0 rows with no error, so a save asks
// for the row back and treats none as refused. 23514 is a CHECK constraint refusing a value.
import type { SupabaseClient } from '@supabase/supabase-js'
import type { FlashLed, FlashMode } from './flashSettings'
import { NOT_ALLOWED } from './projectDetails'

export interface ProjectDefaults {
  id: string
  name: string
  capture_method_id: number | null
  model_id: string | null
  photos_per_trigger: number
  photo_interval_milliseconds: number
  detection_threshold_pct: number
  flash_mode: FlashMode
  flash_led: FlashLed
  flash_window_start_minutes_utc: number | null
  flash_window_minutes: number | null
  lorawan_required: boolean
  lorawan_ping_minutes: number
}

export const PROJECT_DEFAULT_COLUMNS =
  'id, name, capture_method_id, model_id, photos_per_trigger, photo_interval_milliseconds, ' +
  'detection_threshold_pct, flash_mode, flash_led, flash_window_start_minutes_utc, flash_window_minutes, ' +
  'lorawan_required, lorawan_ping_minutes'

export type DefaultsPatch = Partial<Omit<ProjectDefaults, 'id' | 'name'>>
export type DefaultsSave = { saved: ProjectDefaults } | { message: string }

/** Saves the change and returns the row as the database now holds it, or the message to show. */
export async function saveProjectDefaults(
  db: SupabaseClient,
  projectId: string,
  patch: DefaultsPatch,
): Promise<DefaultsSave> {
  const { data, error } = await db.from('projects').update(patch).eq('id', projectId).select(PROJECT_DEFAULT_COLUMNS)
  const saved = (data as ProjectDefaults[] | null)?.[0]
  if (saved) return { saved }
  if (!error) return { message: NOT_ALLOWED }
  return { message: error.code === '23514' ? `That value is not allowed: ${error.message}` : `Not saved: ${error.message}` }
}
