// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Thumbnails for a list of photo ids, resolved the way the grid resolves them (UI audit 03 #16):
// the media row and its media_assets renditions read as the user, then `displayImageUrl`. Unlike
// the Media Registry route it needs no feature flag and has no 200-photo cap.
import type { SupabaseClient } from '@supabase/supabase-js'
import { displayImageUrl } from './mediaImageUrl'

/** Ids per request: 100 uuids keep the `in.(...)` filter well inside a URL's length limit. */
export const THUMB_CHUNK = 100

interface ThumbRow {
  id: string
  file_name: string | null
  file_path: string | null
  media_assets: { thumbnail_url: string | null; preview_url: string | null } | { thumbnail_url: string | null; preview_url: string | null }[] | null
}

/**
 * Photo id to thumbnail URL, or null when the photo has no rendition yet and a private original
 * (show a placeholder, #305). Ids of deleted or unreadable photos are left out.
 */
export async function fetchMediaThumbs(db: SupabaseClient, ids: readonly string[]): Promise<Record<string, string | null>> {
  const unique = [...new Set(ids)]
  const chunks: string[][] = []
  for (let i = 0; i < unique.length; i += THUMB_CHUNK) chunks.push(unique.slice(i, i + THUMB_CHUNK))
  const answers = await Promise.all(chunks.map(chunk => db
    .from('media')
    .select('id, file_name, file_path, media_assets(thumbnail_url, preview_url)')
    .in('id', chunk)
    .is('deleted_at', null)))
  const out: Record<string, string | null> = {}
  for (const { data, error } of answers) {
    if (error) throw new Error(error.message)
    for (const row of (data ?? []) as unknown as ThumbRow[]) out[row.id] = displayImageUrl(row, 'thumb')
  }
  return out
}
