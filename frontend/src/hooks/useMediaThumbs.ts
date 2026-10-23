// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchMediaThumbs } from '../lib/mediaThumbs'

const NONE: Record<string, string | null> = {}

/**
 * Thumbnail URL per photo id, resolved like the grid's cards (`lib/mediaThumbs.ts`). Ask only
 * for the ids on screen: one request per 100 ids, cached by the id list.
 */
export function useMediaThumbs(mediaIds: readonly string[]): Record<string, string | null> {
  const ids = [...new Set(mediaIds)].sort()
  const { data } = useQuery({
    queryKey: ['mediaThumbs', ids],
    queryFn: () => fetchMediaThumbs(supabase, ids),
    enabled: ids.length > 0,
    staleTime: 5 * 60_000,
    // Keep the previous page's thumbnails while the next page's arrive.
    placeholderData: keepPreviousData,
  })
  return data ?? NONE
}
