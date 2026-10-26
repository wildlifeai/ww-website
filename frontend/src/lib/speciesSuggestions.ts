// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// What the species picker offers before it searches (UI audit 03 #15): the species this person
// picked recently, then the species already used in the projects in view, from the view
// project_species (ww-backend #338), or the species labelled on the photos in view until that has
// loaded. The recent picks live in memory for this tab only (nothing is persisted in
// localStorage, UI audit chrome rule). iNaturalist is asked only when fewer than INAT_BELOW local
// suggestions match.
import type { SupabaseClient } from '@supabase/supabase-js'
import { chunk, readAllPages } from './readAllPages'

export interface SpeciesOption {
  taxon_id: string | null
  scientific_name: string
  vernacular_name: string | null
}

/** Ask iNaturalist only when local suggestions are fewer than this. */
export const INAT_BELOW = 3
const RECENT_MAX = 6

/** One key per species: its taxon, else its name. */
export function speciesKey(s: { taxon_id?: string | null; scientific_name: string }): string {
  return s.taxon_id ?? `name:${s.scientific_name.toLowerCase()}`
}

let recent: SpeciesOption[] = []
const listeners = new Set<() => void>()

/** The species picked recently in this tab, newest first (a stable array between picks). */
export function recentSpecies(): SpeciesOption[] {
  return recent
}

/** Records a pick; the picker calls it on every choice. */
export function rememberSpecies(s: SpeciesOption): void {
  const key = speciesKey(s)
  recent = [s, ...recent.filter(r => speciesKey(r) !== key)].slice(0, RECENT_MAX)
  listeners.forEach(l => l())
}

/** For useSyncExternalStore. */
export function subscribeRecentSpecies(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Test hook: forget the recent picks. */
export function clearRecentSpecies(): void {
  recent = []
  listeners.forEach(l => l())
}

/**
 * The species already labelled on these rows, most used first. A row counts once, or `uses` times
 * when it says (a project_species row); a later row fills a missing vernacular name. Only rows
 * with a taxon count, so a pick keeps the picker's taxon_id contract.
 */
export function labelledSpecies(
  rows: Iterable<{ taxon_id?: string | null; scientific_name: string | null; vernacular_name?: string | null; uses?: number }>,
  limit = 20,
): SpeciesOption[] {
  const seen = new Map<string, { option: SpeciesOption; uses: number }>()
  for (const r of rows) {
    if (!r.taxon_id || !r.scientific_name) continue
    const uses = r.uses ?? 1
    const hit = seen.get(r.taxon_id)
    if (hit) {
      hit.uses += uses
      hit.option.vernacular_name ??= r.vernacular_name ?? null
      continue
    }
    seen.set(r.taxon_id, {
      option: { taxon_id: r.taxon_id, scientific_name: r.scientific_name, vernacular_name: r.vernacular_name ?? null },
      uses,
    })
  }
  return [...seen.values()]
    .sort((a, b) => b.uses - a.uses || a.option.scientific_name.localeCompare(b.option.scientific_name))
    .slice(0, limit)
    .map(s => s.option)
}

/** A row of the view project_species: a species named in a project, and how many live observations name it. */
export interface ProjectSpeciesRow {
  taxon_id: string | null
  scientific_name: string
  vernacular_name: string | null
  uses: number
}

/** Project ids per request, so the `in.(...)` list stays well inside the URL limit. */
export const PROJECTS_PER_REQUEST = 150

/**
 * The project_species rows of these projects that carry a taxon: one request for up to
 * `PROJECTS_PER_REQUEST` projects, paged past 1,000 rows on the view's group key. No ids, no
 * request. Give the rows to `labelledSpecies` for one suggestion per species, most used first.
 */
export async function fetchProjectSpecies(db: SupabaseClient, projectIds: string[]): Promise<ProjectSpeciesRow[]> {
  const parts = await Promise.all(chunk(projectIds, PROJECTS_PER_REQUEST).map(ids => readAllPages<ProjectSpeciesRow>(
    (from, to) => db
      .from('project_species')
      .select('taxon_id, scientific_name, vernacular_name, uses')
      .in('project_id', ids)
      .not('taxon_id', 'is', null)
      .order('project_id').order('taxon_id').order('scientific_name').order('vernacular_name')
      .range(from, to),
  )))
  return parts.flat()
}

/**
 * Recent picks, then labelled species, without repeats, whose either name contains the query
 * (case-insensitive). An empty query keeps them all.
 */
export function seedMatches(query: string, recents: SpeciesOption[], labelled: SpeciesOption[], limit = 8): SpeciesOption[] {
  const q = query.trim().toLowerCase()
  const keys = new Set<string>()
  const out: SpeciesOption[] = []
  for (const s of [...recents, ...labelled]) {
    const key = speciesKey(s)
    if (keys.has(key)) continue
    keys.add(key)
    if (q && !s.scientific_name.toLowerCase().includes(q) && !(s.vernacular_name ?? '').toLowerCase().includes(q)) continue
    out.push(s)
    if (out.length >= limit) break
  }
  return out
}
