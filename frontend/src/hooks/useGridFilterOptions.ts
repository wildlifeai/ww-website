// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridFilterOptions: the review grid's species, AI model and annotator options, read over the
// whole selection rather than the loaded page (UI audit 02 #4). Each list is read only once it
// is wanted (the species menu opened, the Advanced filters dialog opened) and kept for five
// minutes, since a large selection takes several requests (lib/distinctValues).
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchDistinctValues, type DistinctValues } from '../lib/distinctValues'

const KEEP_MS = 5 * 60_000

function useDistinct(column: string, deploymentIds: readonly string[], wanted: boolean, where?: Record<string, string>) {
  return useQuery<DistinctValues>({
    queryKey: ['grid-options', column, where ?? null, deploymentIds],
    queryFn: () => fetchDistinctValues(supabase, { column, deploymentIds, where }),
    enabled: wanted && deploymentIds.length > 0,
    staleTime: KEEP_MS,
  })
}

/** "speciesnet-4.0.1a" reads "SpeciesNet (4.0.1a)"; anything else stays as written. */
export function modelOptionLabel(v: string): string {
  if (v.startsWith('speciesnet')) return `SpeciesNet (${v.split('-').pop()})`
  if (v.startsWith('bioclip')) return `BioCLIP (${v.split('-').pop()})`
  return v
}

export function useGridFilterOptions(deploymentIds: readonly string[], want: { species: boolean; advanced: boolean }) {
  const species = useDistinct('scientific_name', deploymentIds, want.species)
  const models = useDistinct('source_model_version', deploymentIds, want.advanced)
  const annotators = useDistinct('classified_by', deploymentIds, want.advanced, { classification_method: 'human' })
  return { species, models, annotators }
}
