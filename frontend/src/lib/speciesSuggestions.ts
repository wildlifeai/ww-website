// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// What the species picker offers before it searches (UI audit 03 #15): the species this person
// picked recently, then the species already labelled on the photos in view. The recent picks live
// in memory for this tab only (nothing is persisted in localStorage, UI audit chrome rule).
// iNaturalist is asked only when fewer than INAT_BELOW local suggestions match.

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
 * The species already labelled on these rows, most used first. Only rows with a taxon count, so a
 * pick keeps the picker's taxon_id contract.
 */
export function labelledSpecies(
  rows: Iterable<{ taxon_id?: string | null; scientific_name: string | null; vernacular_name?: string | null }>,
  limit = 20,
): SpeciesOption[] {
  const seen = new Map<string, { option: SpeciesOption; uses: number }>()
  for (const r of rows) {
    if (!r.taxon_id || !r.scientific_name) continue
    const hit = seen.get(r.taxon_id)
    if (hit) { hit.uses += 1; continue }
    seen.set(r.taxon_id, {
      option: { taxon_id: r.taxon_id, scientific_name: r.scientific_name, vernacular_name: r.vernacular_name ?? null },
      uses: 1,
    })
  }
  return [...seen.values()]
    .sort((a, b) => b.uses - a.uses || a.option.scientific_name.localeCompare(b.option.scientific_name))
    .slice(0, limit)
    .map(s => s.option)
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
