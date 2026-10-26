// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A photo's human verdict (UI audit decision 2, 21 October 2026,
// documentation/development reports/2026-10_ui-audit/README.md). When a person labels, corrects or
// confirms a photo, the label goes on one row they own (`source_type='human'`), updated when the
// photo already has one, and every AI row stays as the model wrote it. So "AI said X, a person
// said Y" survives, and the QA agreement can be measured. Confirming the AI's species is a human
// row that copies the verdict's species.
//
// The viewer and the grid's bulk label write through here. Cluster Review writes through the
// backend (`backend/app/domain/human_verdicts.py`) under the same rule, except that a cluster
// confirm skips a photo a person already labelled. Each write here returns the photo's rows as the
// database now holds them and an `undo` that puts them back. Updates go through
// `updateObservations`, so an update RLS refuses (0 rows) throws NOT_SAVED instead of reading as
// saved; a refused insert throws the same message.
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  humanCreateFields, humanReviewFields, isHumanReviewed, isHumanRow, photoVerdict, type Actor, type PresenceFields,
} from './observations'
import { NOT_REMOVED, NOT_SAVED, updateObservations } from './observationWrites'

/** CamtrapDP observation types (the observations CHECK constraint). */
export type VerdictType = 'animal' | 'human' | 'vehicle' | 'blank' | 'unknown'
const VERDICT_TYPES: ReadonlySet<string> = new Set(['animal', 'human', 'vehicle', 'blank', 'unknown'])

/** What a person says a photo shows. Species and count apply to an animal; count to a person. */
export interface VerdictLabel {
  observation_type: VerdictType
  taxon_id?: string | null
  scientific_name?: string | null
  vernacular_name?: string | null
  count?: number | null
}

/** The row fields the helper reads; the viewer's and the grid's observation types satisfy it. */
export interface VerdictRow extends PresenceFields {
  id: string
  taxon_id?: string | null
  vernacular_name?: string | null
  count?: number | null
  bbox_x?: number | null
  bbox_y?: number | null
  bbox_w?: number | null
  bbox_h?: number | null
}

export interface VerdictPhoto<T extends VerdictRow> {
  id: string
  deployment_id: string
  observations: readonly T[]
}

export interface VerdictWrite<T> {
  /** The photo's rows after the write. */
  observations: T[]
  /** The human row that now carries the verdict (null after a removal). */
  rowId: string | null
  /** Puts the photo's rows back as they were; resolves to the rows after the undo. */
  undo: () => Promise<T[]>
}

const NO_BOX = { bbox_x: null, bbox_y: null, bbox_w: null, bbox_h: null }
// `.in('id', ...)` puts the ids in the URL; 100 uuids stay well inside any proxy's limit.
const ID_CHUNK = 100

/** The columns a verdict writes. A blank has no species, count or box; a person has no species. */
export function verdictFields(label: VerdictLabel): Record<string, unknown> {
  const type = label.observation_type
  const count = label.count != null && Number.isInteger(label.count) && label.count >= 1 ? label.count : null
  const named = type === 'animal'
  return {
    observation_type: type,
    taxon_id: named ? label.taxon_id ?? null : null,
    scientific_name: named ? label.scientific_name ?? null : null,
    vernacular_name: named ? label.vernacular_name ?? null : null,
    count: type === 'blank' ? null : count,
    ...(type === 'blank' ? NO_BOX : {}),
  }
}

/** A row's label as a verdict, so Confirm can copy it. */
export function labelOf(o: VerdictRow): VerdictLabel {
  const named = !!o.scientific_name || !!o.vernacular_name
  const type = o.observation_type && VERDICT_TYPES.has(o.observation_type)
    ? (named ? 'animal' : o.observation_type as VerdictType)
    : (named ? 'animal' : 'unknown')
  return {
    observation_type: type,
    taxon_id: o.taxon_id ?? null,
    scientific_name: o.scientific_name ?? null,
    vernacular_name: o.vernacular_name ?? null,
    count: o.count ?? null,
  }
}

/** What Confirm on the whole photo agrees to: the verdict the card shows, or null for none. */
export function confirmLabel(obs: readonly VerdictRow[]): VerdictLabel | null {
  const { labelObs, isEmpty } = photoVerdict(obs)
  if (isEmpty) return { observation_type: 'blank' }
  return labelObs ? labelOf(labelObs) : null
}

/** True when a person already ruled on the photo's verdict, so Confirm has nothing to add. */
export function verdictIsReviewed(obs: readonly VerdictRow[]): boolean {
  const { labelObs } = photoVerdict(obs)
  return !!labelObs && isHumanReviewed(labelObs)
}

export type VerdictPlan =
  | { kind: 'insert'; row: Record<string, unknown> }
  | { kind: 'update'; ids: string[]; patch: Record<string, unknown> }

/**
 * The write a verdict needs: an insert when the photo has no human row, else an update of its
 * human rows. Older photos can carry several (the bulk label used to add one each time); they all
 * take the verdict, so the photo keeps one answer.
 */
export function planVerdict(photo: VerdictPhoto<VerdictRow>, label: VerdictLabel, actor: Actor): VerdictPlan {
  const human = photo.observations.filter(isHumanRow)
  if (human.length === 0) return { kind: 'insert', row: newVerdictRow(photo, label, actor) }
  return { kind: 'update', ids: human.map(o => o.id), patch: { ...verdictFields(label), ...humanReviewFields(actor) } }
}

function newVerdictRow(photo: VerdictPhoto<VerdictRow>, label: VerdictLabel, actor: Actor): Record<string, unknown> {
  return {
    deployment_id: photo.deployment_id, media_id: photo.id, observation_level: 'media',
    ...verdictFields(label), ...humanCreateFields(actor),
  }
}

/** The values `keys` had on `row`, to put back on undo. Fields the row was loaded without are left out. */
export function snapshot(row: object, keys: string[]): Record<string, unknown> {
  const r = row as Record<string, unknown>
  return Object.fromEntries(keys.filter(k => r[k] !== undefined).map(k => [k, r[k]]))
}

function chunks<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size))
  return out
}

function refusal(error: { code?: string; message: string }): Error {
  return new Error(error.code === '42501' ? NOT_SAVED : error.message)
}

/** Inserts rows as the caller and returns them as stored. A refused insert throws NOT_SAVED. */
export async function insertObservations<T>(db: SupabaseClient, rows: Record<string, unknown>[]): Promise<T[]> {
  const parts = await Promise.all(chunks(rows, 500).map(async part => {
    const { data, error } = await db.from('observations').insert(part).select()
    if (error) throw refusal(error)
    if (!data || data.length < part.length) throw new Error(NOT_SAVED)
    return data as T[]
  }))
  return parts.flat()
}

/** Deletes rows as the caller and returns them in full, so an undo can put them back. */
export async function deleteObservationsReturning<T>(db: SupabaseClient, ids: string[]): Promise<T[]> {
  const parts = await Promise.all(chunks(ids, ID_CHUNK).map(async part => {
    const { data, error } = await db.from('observations').delete().in('id', part).select()
    if (error) throw new Error(error.message)
    return (data ?? []) as T[]
  }))
  const out = parts.flat()
  if (ids.length > 0 && out.length === 0) throw new Error(NOT_REMOVED)
  return out
}

/** Updates in chunks; returns the ids changed. Throws NOT_SAVED when none changed. */
async function updateAll(db: SupabaseClient, ids: string[], patch: Record<string, unknown>): Promise<string[]> {
  return (await Promise.all(chunks(ids, ID_CHUNK).map(part => updateObservations(db, part, patch)))).flat()
}

/** Puts rows' earlier values back, one update per distinct set of values (usually one). */
async function restore(db: SupabaseClient, before: object[], keys: string[]): Promise<void> {
  const groups = new Map<string, { ids: string[]; patch: Record<string, unknown> }>()
  for (const row of before) {
    const patch = snapshot(row, keys)
    const key = JSON.stringify(patch)
    const group = groups.get(key) ?? { ids: [], patch }
    group.ids.push((row as { id: string }).id)
    groups.set(key, group)
  }
  await Promise.all([...groups.values()].map(({ ids, patch }) => updateAll(db, ids, patch)))
}

/** Writes one photo's verdict: inserts its human row or updates it. One request either way. */
export async function writeVerdict<T extends VerdictRow>(
  db: SupabaseClient, photo: VerdictPhoto<T>, label: VerdictLabel, actor: Actor,
): Promise<VerdictWrite<T>> {
  const plan = planVerdict(photo, label, actor)
  const was = [...photo.observations]
  if (plan.kind === 'insert') {
    const [row] = await insertObservations<T>(db, [plan.row])
    return {
      observations: [...was, row],
      rowId: row.id,
      undo: async () => { await deleteObservationsReturning(db, [row.id]); return was },
    }
  }
  const changed = new Set(await updateAll(db, plan.ids, plan.patch))
  const before = was.filter(o => changed.has(o.id))
  return {
    observations: was.map(o => changed.has(o.id) ? { ...o, ...plan.patch } : o),
    rowId: plan.ids.find(id => changed.has(id)) ?? null,
    undo: async () => { await restore(db, before, Object.keys(plan.patch)); return was },
  }
}

export interface BulkVerdictWrite<T> {
  /** Photos whose verdict was written. */
  saved: number
  /** Each written photo's rows after the write, by media id. */
  observations: Map<string, T[]>
  /** Puts every written photo back as it was. */
  undo: () => Promise<void>
}

/**
 * Writes one verdict on many photos: one insert for the photos without a human row and one update
 * for the rest (chunked). If the update is refused the inserts are taken back, so a refusal leaves
 * nothing half done.
 */
export async function writeVerdicts<T extends VerdictRow>(
  db: SupabaseClient, photos: VerdictPhoto<T>[], label: VerdictLabel, actor: Actor,
): Promise<BulkVerdictWrite<T>> {
  const toInsert = photos.filter(p => !p.observations.some(isHumanRow))
  const toUpdate = photos.filter(p => p.observations.some(isHumanRow))
  const inserted = await insertObservations<T>(db, toInsert.map(p => newVerdictRow(p, label, actor)))
  const insertedIds = inserted.map(r => r.id)
  const patch = { ...verdictFields(label), ...humanReviewFields(actor) }
  const before = toUpdate.flatMap(p => p.observations.filter(isHumanRow))
  let changed = new Set<string>()
  if (before.length > 0) {
    try {
      changed = new Set(await updateAll(db, before.map(o => o.id), patch))
    } catch (e) {
      if (insertedIds.length > 0) await deleteObservationsReturning(db, insertedIds)
      throw e
    }
  }
  const observations = new Map<string, T[]>()
  const insertedFor = new Map(toInsert.map(p => [p.id, p]))
  for (const row of inserted) {
    const photo = insertedFor.get((row as { media_id?: string }).media_id ?? '')
    if (photo) observations.set(photo.id, [...photo.observations, row])
  }
  for (const p of toUpdate) {
    if (!p.observations.some(o => changed.has(o.id))) continue
    observations.set(p.id, p.observations.map(o => changed.has(o.id) ? { ...o, ...patch } : o))
  }
  const restored = before.filter(o => changed.has(o.id))
  return {
    saved: observations.size,
    observations,
    undo: async () => {
      if (insertedIds.length > 0) await deleteObservationsReturning(db, insertedIds)
      if (restored.length > 0) await restore(db, restored, Object.keys(patch))
    },
  }
}

/** Changes a person's own row (its box); AI rows are never edited. */
export async function updateHumanRow<T extends VerdictRow>(
  db: SupabaseClient, photo: VerdictPhoto<T>, rowId: string, fields: Record<string, unknown>, actor: Actor,
): Promise<VerdictWrite<T>> {
  const row = photo.observations.find(o => o.id === rowId)
  if (!row || !isHumanRow(row)) throw new Error('Only your own label can be changed; AI rows stay as the model wrote them.')
  const patch = { ...fields, ...humanReviewFields(actor) }
  await updateAll(db, [rowId], patch)
  const was = [...photo.observations]
  return {
    observations: was.map(o => o.id === rowId ? { ...o, ...patch } : o),
    rowId,
    undo: async () => { await restore(db, [row], Object.keys(patch)); return was },
  }
}

/** Removes a person's own row, which hands the photo's verdict back to the AI. Undo re-inserts it. */
export async function removeHumanRow<T extends VerdictRow>(
  db: SupabaseClient, photo: VerdictPhoto<T>, rowId: string,
): Promise<VerdictWrite<T>> {
  const row = photo.observations.find(o => o.id === rowId)
  if (!row || !isHumanRow(row)) throw new Error('Only your own label can be removed; AI rows stay as the model wrote them.')
  const [stored] = await deleteObservationsReturning<T>(db, [rowId])
  const was = [...photo.observations]
  return {
    observations: was.filter(o => o.id !== rowId),
    rowId: null,
    undo: async () => {
      const [back] = await insertObservations<T>(db, [{ ...(stored ?? row) } as Record<string, unknown>])
      return was.map(o => o.id === rowId ? { ...o, ...back } : o)
    },
  }
}
