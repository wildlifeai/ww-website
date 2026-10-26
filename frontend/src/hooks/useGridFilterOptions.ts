// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridFilterOptions: the review grid's species, AI model and annotator options, read over the
// whole selection rather than the loaded page (UI audit 02 #4). One `grid_facets` call gives all
// three lists (lib/gridFacets), read once a list is wanted (the species menu opened, the
// Advanced filters dialog opened) and kept for five minutes.
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchGridFacets, type GridFacets } from '../lib/gridFacets'

const KEEP_MS = 5 * 60_000

/** "speciesnet-4.0.1a" reads "SpeciesNet (4.0.1a)"; anything else stays as written. */
export function modelOptionLabel(v: string): string {
  if (v.startsWith('speciesnet')) return `SpeciesNet (${v.split('-').pop()})`
  if (v.startsWith('bioclip')) return `BioCLIP (${v.split('-').pop()})`
  return v
}

export function useGridFilterOptions(deploymentIds: readonly string[], wanted: boolean) {
  return useQuery<GridFacets>({
    queryKey: ['grid-facets', deploymentIds],
    queryFn: () => fetchGridFacets(supabase, deploymentIds),
    enabled: wanted && deploymentIds.length > 0,
    staleTime: KEEP_MS,
  })
}
