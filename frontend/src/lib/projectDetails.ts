// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Edit a project's name, description and website (#288). The write runs as the signed-in user.
// RLS lets a project_admin of the project, or a ww_admin, update it, and turns anyone else's
// update into 0 rows with no error, so a save asks for the row back and treats none as refused.
// The columns carry no length, format or uniqueness checks; only `name` is required (NOT NULL).
import type { SupabaseClient } from '@supabase/supabase-js'

export interface ProjectDetails {
  id: string
  name: string
  description: string | null
  website: string | null
}

export const PROJECT_DETAIL_COLUMNS = 'id, name, description, website'

export interface DetailsForm {
  name: string
  description: string
  website: string
}

export const NOT_ALLOWED = 'You need the Project Admin role to change this.'

export function formFromDetails(p: ProjectDetails): DetailsForm {
  return { name: p.name ?? '', description: p.description ?? '', website: p.website ?? '' }
}

/** The message to show, or null when the form can be saved. */
export function validateDetails(form: DetailsForm): string | null {
  return form.name.trim() ? null : 'A project name is required.'
}

/** Trimmed, with a blank description or website saved as null. */
export function buildDetailsPatch(form: DetailsForm): Omit<ProjectDetails, 'id'> {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    website: form.website.trim() || null,
  }
}

/** The project as the database holds it, or null when the user cannot see it. */
export async function fetchProjectDetails(db: SupabaseClient, projectId: string): Promise<ProjectDetails | null> {
  const { data, error } = await db.from('projects').select(PROJECT_DETAIL_COLUMNS).eq('id', projectId).maybeSingle()
  if (error) throw new Error(error.message)
  return data as ProjectDetails | null
}

/** Saves and returns the stored row; throws a message the user can read when it is not saved. */
export async function saveProjectDetails(
  db: SupabaseClient,
  projectId: string,
  patch: Omit<ProjectDetails, 'id'>,
): Promise<ProjectDetails> {
  const { data, error } = await db.from('projects').update(patch).eq('id', projectId).select(PROJECT_DETAIL_COLUMNS)
  if (error) throw new Error(error.code === '42501' ? NOT_ALLOWED : `Not saved: ${error.message}`)
  const saved = (data as ProjectDetails[] | null)?.[0]
  if (!saved) throw new Error(NOT_ALLOWED)
  return saved
}
