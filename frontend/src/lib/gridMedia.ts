// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The review grid's photo records: the columns it reads, photos read by id in a given order (find
// similar, a linked photo), the cards the Labels view shows, a still-uploading file's placeholder,
// the page's counts and the order a Shift-click range follows.
import type { SupabaseClient } from '@supabase/supabase-js'
import { isHumanReviewed } from './observations'
import type { PendingItem } from './pendingCards'
import { chunk } from './readAllPages'

// Columns fetched for a media record (+ its assets and observations). Shared by
// the paginated grid loader and the "find similar" fetch so they stay in sync.
export const MEDIA_SELECT =
  'id, deployment_id, file_path, file_name, file_mediatype, timestamp, created_at, file_public, media_comments, exif_metadata, ' +
  'media_assets(thumbnail_url, preview_url, animal_crop_url), ' +
  'observations(id, deployment_id, media_id, observation_type, scientific_name, vernacular_name, taxon_id, ' +
  'count, life_stage, sex, behavior, ' +
  'classification_method, classified_by, classification_timestamp, classification_probability, observation_comments, crop_url, ' +
  'review_status, source_type, ai_origin, source_model_version, reviewer_id, annotator_id, bbox_x, bbox_y, bbox_w, bbox_h)'

export interface MediaAssetRecord {
  thumbnail_url: string | null
  preview_url: string | null
  animal_crop_url: string | null
}

export interface MediaRecord {
  id: string
  deployment_id: string
  file_path: string
  file_name: string | null
  file_mediatype: string
  timestamp: string | null
  /** Row creation time; lets the grid retire an optimistic card once its real row has landed. */
  created_at?: string | null
  file_public: boolean
  media_comments: string | null
  exif_metadata: Record<string, unknown> | null
  // 1:1 rendition row (MEDIA_PREP): public Supabase Storage URLs for the grid.
  // PostgREST returns this to-one embed as a single object (media_id is the PK),
  // but tolerate an array too in case the relationship is ever re-detected.
  media_assets: MediaAssetRecord | MediaAssetRecord[] | null
  observations: ObservationRecord[]
  /** Optimistic placeholder for a still-uploading file (not a real DB row). Display-only. */
  _pending?: boolean
}

export interface ObservationRecord {
  id: string
  deployment_id: string
  media_id: string | null
  observation_type: string | null
  scientific_name: string | null
  vernacular_name?: string | null
  taxon_id?: string | null
  count: number | null
  life_stage: string | null
  sex: string | null
  behavior: string | null
  classification_method: string | null
  classified_by: string | null
  classification_timestamp?: string | null
  classification_probability: number | null
  observation_comments: string | null
  crop_url?: string | null   // per-observation bbox crop (crop view)
  // AN-1/AN-2: validation provenance + lifecycle (authoritative for status)
  review_status?: string | null
  source_type?: string | null
  /** For source_type='ai': which AI layer produced the row, 'edge' (Camera AI) or 'cloud'. */
  ai_origin?: string | null
  source_model_version?: string | null
  reviewer_id?: string | null
  annotator_id?: string | null
  bbox_x?: number | null
  bbox_y?: number | null
  bbox_w?: number | null
  bbox_h?: number | null
}

/** Photo ids per request when reading photos by id. */
const IDS_PER_REQUEST = 100

/** Media records for `ids`, in that order (deleted photos dropped), 100 ids per request. */
export async function fetchMediaInOrder(db: SupabaseClient, ids: readonly string[]): Promise<MediaRecord[]> {
  const rank = new Map(ids.map((id, i) => [id, i]))
  const pages = await Promise.all(chunk([...ids], IDS_PER_REQUEST).map(async part => {
    const { data, error } = await db.from('media').select(MEDIA_SELECT).in('id', part).is('deleted_at', null)
    if (error) throw new Error(error.message)
    return (data || []) as unknown as MediaRecord[]
  }))
  return pages.flat().sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0))
}

/** Labels view: at most this many cards per photo; the viewer shows the rest. */
export const MAX_LABEL_CARDS = 4

// Which observations get a card in the "Labels" view: AI detections that have a
// crop, plus any human-made or human-reviewed label (these usually have no crop,
// so they're shown on the full frame). AI detections with no crop are omitted.
export function isLabelCard(o: ObservationRecord): boolean {
  return !!o.crop_url || o.source_type === 'human' || isHumanReviewed(o)
}

/** A photo's cards in the Labels view, at most `MAX_LABEL_CARDS`. */
export const labelCards = (m: MediaRecord): ObservationRecord[] => m.observations.filter(isLabelCard).slice(0, MAX_LABEL_CARDS)

/** The card of a file still uploading: display only, never a database row. */
export function pendingMediaRecord(p: PendingItem): MediaRecord {
  return {
    id: `pending:${p.fileName.toLowerCase()}`, deployment_id: p.deploymentId, file_path: '', file_name: p.fileName,
    file_mediatype: 'image/jpeg', timestamp: null, file_public: false, media_comments: null,
    exif_metadata: null, media_assets: null, observations: [], _pending: true,
  }
}

export interface PageStats {
  /** Real photos on the page. */
  total: number
  /** Placeholders of files still uploading. */
  uploading: number
  /** Real photos with no hosted file. */
  noImage: number
}

/**
 * The page's counts over real rows only: a placeholder has no file_path by construction, and
 * counting it as "no image" (and as a photo) made the header read "10 no image" during every upload.
 */
export function pageStats(cards: readonly MediaRecord[]): PageStats {
  const real = cards.filter(m => !m._pending)
  return { total: real.length, uploading: cards.length - real.length, noImage: real.filter(m => !m.file_path).length }
}

/**
 * The selectable photos in the order the grid shows them, for a Shift-click range. In the Labels
 * view a photo with no label card has no card, so it is not in the range.
 */
export function selectableInOrder(cards: readonly MediaRecord[], isSelectable: (m: MediaRecord) => boolean, labelsView: boolean): string[] {
  return cards.flatMap(m => (isSelectable(m) && (!labelsView || m.observations.some(isLabelCard)) ? [m.id] : []))
}
