// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useProjectSpecies: the species already used in these projects, most used first, for the species
// picker's "Labelled here" suggestions (view project_species, ww-backend #338). One read per set of
// projects, cached by the sorted ids for five minutes and never polled, so opening the picker again
// on the same project asks nothing. Undefined with no ids, while loading and after an error: the
// picker then offers the species on the photos in view, as before.
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { fetchProjectSpecies, labelledSpecies, type SpeciesOption } from '../lib/speciesSuggestions'

const STALE_MS = 5 * 60_000

export function useProjectSpecies(projectIds: readonly string[] | undefined, enabled = true): SpeciesOption[] | undefined {
  const ids = [...new Set(projectIds ?? [])].sort()
  const species = useQuery({
    queryKey: ['project-species', ids],
    queryFn: async () => labelledSpecies(await fetchProjectSpecies(supabase, ids), Infinity),
    enabled: enabled && ids.length > 0,
    staleTime: STALE_MS,
  })
  return ids.length > 0 ? species.data : undefined
}
