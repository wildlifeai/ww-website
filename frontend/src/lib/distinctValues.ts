// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The distinct values of one observations column over a set of deployments, for the review
// grid's option lists (UI audit 02 #4): the species list covers the whole selection, not the
// 100 photos on the page. PostgREST has no DISTINCT, so this reads in value order and, after
// each page, skips past the last value it saw (`gt`). Each request returns up to 1,000 rows and
// at least one new value, so it takes about one request per value with more than a page of
// rows, not one per 1,000 observations. Live rows only, as lib/liveObservations reads them:
// not soft-deleted, on a photo that is not deleted.
import type { SupabaseClient } from '@supabase/supabase-js'
import { PAGE_SIZE } from './liveObservations'

/** Stops a pathological column from reading forever; the list is then marked incomplete. */
export const MAX_REQUESTS = 60

export interface DistinctRead {
  /** An observations column, e.g. `scientific_name`. */
  column: string
  deploymentIds: readonly string[]
  /** Extra equality filters, e.g. `{ classification_method: 'human' }`. */
  where?: Readonly<Record<string, string>>
}

export interface DistinctValues {
  values: string[]
  /** True when MAX_REQUESTS ran out before the last value. */
  incomplete: boolean
}

export async function fetchDistinctValues(db: SupabaseClient, read: DistinctRead): Promise<DistinctValues> {
  const { column, deploymentIds, where = {} } = read
  const values: string[] = []
  if (deploymentIds.length === 0) return { values, incomplete: false }
  let last: string | null = null
  for (let n = 0; n < MAX_REQUESTS; n++) {
    let q = db.from('observations').select(`${column}, media!inner(deleted_at)`)
      .in('deployment_id', [...deploymentIds])
      .is('deleted_at', null)
      .is('media.deleted_at', null)
      .not(column, 'is', null)
    for (const [k, v] of Object.entries(where)) q = q.eq(k, v)
    if (last !== null) q = q.gt(column, last)
    const { data, error } = await q.order(column).limit(PAGE_SIZE)
    if (error) throw new Error(error.message)
    const rows = (data ?? []) as unknown as Record<string, unknown>[]
    for (const row of rows) {
      const v = row[column]
      if (typeof v === 'string' && v !== values[values.length - 1]) values.push(v)
    }
    if (rows.length < PAGE_SIZE) return { values, incomplete: false }
    const next = values[values.length - 1] ?? null
    if (next === null || next === last) return { values, incomplete: false }
    last = next
  }
  return { values, incomplete: true }
}
