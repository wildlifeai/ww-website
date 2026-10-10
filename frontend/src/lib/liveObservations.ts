/**
 * Reads observations that are still live, for every count, chart and species list.
 *
 * Deleting photos soft-deletes their `media` rows only. Their observations stay,
 * and the database still returns them: the `observations` read policy checks the
 * deployment's `deleted_at`, not the photo's (#198). So a read that counts or
 * lists observations drops the ones whose photo is deleted, here, in one place.
 *
 * - `requirePhoto: false` (the default) also keeps observations with no photo
 *   (`media_id` null, CamtrapDP event-level rows), which are real records.
 * - `requirePhoto: true` keeps only observations on a live photo, for summaries
 *   of what the photos show.
 *
 * The read pages with `.range()`, because PostgREST stops at 1,000 rows without
 * an error and a deployment can hold more detections than that.
 */
import type { SupabaseClient } from '@supabase/supabase-js'

/** The PostgREST row cap, and the page size used to read past it. */
export const PAGE_SIZE = 1000

/** A select on `observations`, typed loosely: the columns are a runtime string. */
function selectObservations(db: SupabaseClient, select: string) {
  return db.from('observations').select(select)
}

type ObservationQuery = ReturnType<typeof selectObservations>

export interface LiveObservationsRead {
  /** Observation columns, as for `.select()`. */
  columns: string
  /** Media columns the caller reads, returned on each row under `media`. */
  mediaColumns?: string
  /** Keep only observations on a live photo, dropping those with no photo. */
  requirePhoto?: boolean
  /** The caller's own filters (deployment, type, date). */
  filter?: (q: ObservationQuery) => ObservationQuery
  /** Sort order; `id` is always added after it so pages do not overlap. */
  order?: { column: string; ascending?: boolean }
}

export interface LiveObservationsResult<T> {
  data: T[] | null
  error: { message: string } | null
}

/** Every live observation matching the read, across as many pages as it takes. */
export async function fetchLiveObservations<T>(
  db: SupabaseClient, read: LiveObservationsRead,
): Promise<LiveObservationsResult<T>> {
  const { columns, mediaColumns, requirePhoto = false, filter, order } = read
  const embed = `media${requirePhoto ? '!inner' : ''}(deleted_at${mediaColumns ? `, ${mediaColumns}` : ''})`
  const select: string = `${columns}, ${embed}`
  const rows: T[] = []
  for (let from = 0; ; from += PAGE_SIZE) {
    let q = selectObservations(db, select)
    if (filter) q = filter(q)
    q = q.is('deleted_at', null).is('media.deleted_at', null)
    // Without !inner a deleted photo's embed comes back null rather than dropping
    // the row, so keep a row only when it has no photo or its photo survived.
    if (!requirePhoto) q = q.or('media_id.is.null,media.not.is.null')
    if (order) q = q.order(order.column, { ascending: order.ascending ?? true })
    const { data, error } = await q.order('id').range(from, from + PAGE_SIZE - 1)
    if (error) return { data: null, error }
    const page = (data ?? []) as unknown as Record<string, unknown>[]
    for (const row of page) {
      if (!mediaColumns) delete row.media
      rows.push(row as T)
    }
    if (page.length < PAGE_SIZE) return { data: rows, error: null }
  }
}
