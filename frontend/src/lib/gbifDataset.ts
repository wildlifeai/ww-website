// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A project's GBIF dataset (#373), for the GBF report's Target 21. The key is read straight from
// projects; saving goes through PUT /api/projects/{id}/gbif-dataset, which checks the key with
// GBIF and writes as the user, so projects_update_policy lets only a project admin save it.
import type { SupabaseClient } from '@supabase/supabase-js'

export const gbifDatasetUrl = (key: string) => `https://www.gbif.org/dataset/${key}`

/** The project's dataset key, or null when it has none. */
export async function fetchGbifDatasetKey(db: Pick<SupabaseClient, 'from'>, projectId: string): Promise<string | null> {
  const { data, error } = await db.from('projects').select('gbif_dataset_key').eq('id', projectId).maybeSingle()
  if (error) throw new Error(error.message)
  return (data as { gbif_dataset_key?: string | null } | null)?.gbif_dataset_key ?? null
}

export interface SavedGbifDataset {
  gbif_dataset_key: string | null
  dataset: { title: string | null; doi: string | null } | null
}

/** Saves a key or a gbif.org link, or clears it with blank text; throws the message to show. */
export async function saveGbifDataset(
  put: (path: string, body: unknown) => Promise<unknown>,
  projectId: string,
  text: string,
): Promise<SavedGbifDataset> {
  const res = await put(`/api/projects/${projectId}/gbif-dataset`, { dataset: text.trim() || null }) as { data?: SavedGbifDataset }
  if (!res?.data) throw new Error('Not saved.')
  return res.data
}
