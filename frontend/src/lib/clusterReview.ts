// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Ordering and paging for Cluster Review (UI audit 03 #16). The API returns every cluster of the
// latest run in one answer, so the page orders and pages them here. Also the review batching and
// the confirm toast's words.
import { chunk } from './readAllPages'

export const CLUSTERS_PER_PAGE = 10
/** Photos shown at a time in an expanded cluster; "Show more" adds the next step. */
export const MEMBERS_PER_STEP = 120

interface Orderable {
  id: string
  cluster_id: number
  image_count: number
  is_outlier_cluster: boolean
  review_state: string
}

/**
 * The clusters to list, in review order: unconfirmed ones biggest first, then the outlier group,
 * then (only when `showConfirmed`) the confirmed ones. A cluster in `keep` (confirmed during this
 * visit) stays where it was, so the reviewer sees the result where they acted.
 */
export function orderClusters<T extends Orderable>(
  clusters: readonly T[],
  showConfirmed: boolean,
  keep: ReadonlySet<string> = new Set(),
): T[] {
  const rank = (c: T) =>
    keep.has(c.id) ? (c.is_outlier_cluster ? 1 : 0)
      : c.review_state === 'confirmed' ? 2
        : c.is_outlier_cluster ? 1 : 0
  return clusters
    .filter(c => showConfirmed || c.review_state !== 'confirmed' || keep.has(c.id))
    .sort((a, b) => rank(a) - rank(b) || b.image_count - a.image_count || a.cluster_id - b.cluster_id)
}

export interface Page<T> {
  items: T[]
  /** The page shown, clamped to the last one when the list shrank under it. */
  page: number
  pages: number
  /** 1-based position of the first and last item shown, 0 and 0 for an empty list. */
  first: number
  last: number
}

/** One page of `items` (0-based `page`). */
export function pageOf<T>(items: readonly T[], page: number, size: number): Page<T> {
  const pages = Math.max(1, Math.ceil(items.length / size))
  const p = Math.min(Math.max(0, page), pages - 1)
  const slice = items.slice(p * size, p * size + size)
  return { items: slice, page: p, pages, first: slice.length ? p * size + 1 : 0, last: p * size + slice.length }
}

/** Photos per `POST /api/brain/review/batch` request, the route's cap. */
export const REVIEW_BATCH_MAX = 500

/** What `POST /api/brain/review/batch` answers. */
export interface ReviewBatchResult {
  /** Photos given a human row. */
  created: number
  /** Photos whose human row now carries the decision. */
  updated: number
  observation_ids: string[]
  skipped: { media_id: string; reason: string }[]
}

/**
 * Sends one review decision for `mediaIds` in requests of at most REVIEW_BATCH_MAX photos (one
 * request for a selection that size or smaller) and adds up the answers. The requests write
 * different photos, so they go together; a refused one rejects the whole call.
 */
export async function reviewInBatches(
  mediaIds: string[],
  send: (ids: string[]) => Promise<ReviewBatchResult>,
): Promise<{ saved: number; skipped: number }> {
  const answers = await Promise.all(chunk(mediaIds, REVIEW_BATCH_MAX).map(ids => send(ids)))
  return {
    saved: answers.reduce((n, r) => n + r.created + r.updated, 0),
    skipped: answers.reduce((n, r) => n + r.skipped.length, 0),
  }
}

const photoCount = (n: number) => `${n} photo${n === 1 ? '' : 's'}`

/** The confirm toast: what was labelled, and how many photos a person had labelled were skipped. */
export function confirmedText(created: number, skipped: number, species: string): string {
  const held = skipped > 0 ? `; skipped ${photoCount(skipped)} a person had labelled` : ''
  return `Confirmed ${photoCount(created)} as ${species}${held}`
}
