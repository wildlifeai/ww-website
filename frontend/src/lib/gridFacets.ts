// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The review grid's species, AI model and annotator options over the whole selection (UI audit
// 02 #4), from ww-backend's `grid_facets` (#336): each live value with its count of
// observations, in one call per 150 deployments. Annotators are the people behind human labels.
// The function checks access itself and skips deployments the caller cannot read.
import type { SupabaseClient } from '@supabase/supabase-js'
import { chunk, readAllPages } from './readAllPages'

/** Deployment ids per call, as Insights sends them. */
export const IDS_PER_CALL = 150

/** One row of `grid_facets`. */
export interface FacetRow {
  facet: string
  value: string | null
  /** bigint; PostgREST sends it as a number. */
  n: number | string
}

export interface FacetOption {
  value: string
  /** Live observations carrying the value. */
  n: number
}

export interface GridFacets {
  species: FacetOption[]
  models: FacetOption[]
  annotators: FacetOption[]
}

const LISTS: Readonly<Record<string, keyof GridFacets>> = {
  scientific_name: 'species',
  source_model_version: 'models',
  classified_by: 'annotators',
}

// Alphabetical, as the database's collation listed them before (case does not split the list).
const byValue = new Intl.Collator('en').compare

/** The option lists from facet rows, counts summed over chunks, each list alphabetical. */
export function gridFacetOptions(rows: readonly FacetRow[]): GridFacets {
  const counts: Record<keyof GridFacets, Map<string, number>> = { species: new Map(), models: new Map(), annotators: new Map() }
  for (const r of rows) {
    const list = LISTS[r.facet]
    if (!list || typeof r.value !== 'string') continue
    const m = counts[list]
    m.set(r.value, (m.get(r.value) ?? 0) + Number(r.n))
  }
  const options = (m: Map<string, number>) =>
    [...m].map(([value, n]) => ({ value, n })).sort((a, b) => byValue(a.value, b.value))
  return { species: options(counts.species), models: options(counts.models), annotators: options(counts.annotators) }
}

/** The grid's option lists for the deployments; nothing is asked for none. */
export async function fetchGridFacets(db: SupabaseClient, deploymentIds: readonly string[]): Promise<GridFacets> {
  const parts = await Promise.all(chunk([...deploymentIds], IDS_PER_CALL).map(ids =>
    // (facet, value) is unique, so the pages of a long result neither repeat nor skip a row.
    readAllPages<FacetRow>((from, to) =>
      db.rpc('grid_facets', { p_deployment_ids: ids }).order('facet').order('value').range(from, to))))
  return gridFacetOptions(parts.flat())
}
