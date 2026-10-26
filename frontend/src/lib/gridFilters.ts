// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The review grid's filters and order as one PostgREST media query (UI audit 02 #4, #5), so the
// page and the pager's total both reflect every filter, across the whole selection rather than
// the 100 loaded photos. An observation filter is an empty embed of `observations` under its own alias:
// `!inner` keeps photos with a matching row, a plain embed plus `alias=is.null` keeps photos with
// none (an anti-join). One alias per filter, so two filters may be met by two different rows,
// as when they ran in the browser.

import { HUMAN_REVIEWED_STATES } from './observations'
import { DAY_START_HOUR, NIGHT_START_HOUR } from './time'

export type GridStatus = '' | 'ai' | 'reviewed' | 'pending'
export type GridAnnotationType = '' | 'bbox' | 'whole'
export type GridTimeOfDay = '' | 'day' | 'night'

export interface GridFilters {
  species: string[]
  status: GridStatus
  annotator: string
  model: string
  annotationType: GridAnnotationType
  sex: string
  lifeStage: string
  /** YYYY-MM-DD, inclusive; compared in UTC as before. */
  dateFrom: string
  dateTo: string
  /** By the capture hour in the deployment's zone; photos with no capture time match neither. */
  timeOfDay: GridTimeOfDay
}

export const NO_FILTERS: GridFilters = {
  species: [], status: '', annotator: '', model: '', annotationType: '', sex: '', lifeStage: '', dateFrom: '', dateTo: '',
  timeOfDay: '',
}

/** One filter call on the media query, as data so it can be tested without a client. */
export type FilterOp =
  | { op: 'in'; column: string; values: string[] }
  | { op: 'eq'; column: string; value: string }
  | { op: 'gte'; column: string; value: number }
  | { op: 'lt'; column: string; value: number }
  | { op: 'notNull'; column: string }
  | { op: 'isNull'; column: string }
  | { op: 'or'; filters: string; referencedTable?: string }

export interface FilterPlan {
  /** Extra select entries (empty embeds) the ops refer to. */
  embeds: string[]
  ops: FilterOp[]
}

const REVIEWED_STATES = [...HUMAN_REVIEWED_STATES]
/** A row carrying an AI label, as `isAiLabel` reads it. */
const AI_ROW = 'source_type.eq.ai,classification_method.eq.machine,review_status.eq.ai_reviewed'
/** A row that is AI or human reviewed: a photo with none is "not labelled yet". */
const ANY_LABEL = `source_type.eq.ai,classification_method.eq.machine,review_status.in.(ai_reviewed,${REVIEWED_STATES.join(',')})`
const DAY = /^\d{4}-\d{2}-\d{2}$/

/** The embeds and filter calls for a set of filters; no filters gives an empty plan. */
export function gridFilterPlan(f: GridFilters): FilterPlan {
  const embeds: string[] = []
  const ops: FilterOp[] = []
  const withRow = (alias: string) => embeds.push(`${alias}:observations!inner()`)
  const withoutRow = (alias: string) => {
    embeds.push(`${alias}:observations()`)
    return () => ops.push({ op: 'isNull', column: alias })
  }

  if (f.species.length) {
    withRow('f_species')
    ops.push({ op: 'in', column: 'f_species.scientific_name', values: [...f.species] })
  }
  if (f.annotator) {
    withRow('f_annotator')
    ops.push({ op: 'eq', column: 'f_annotator.classification_method', value: 'human' })
    ops.push({ op: 'eq', column: 'f_annotator.classified_by', value: f.annotator })
  }
  if (f.model) {
    withRow('f_model')
    ops.push({ op: 'eq', column: 'f_model.source_model_version', value: f.model })
  }
  if (f.sex) {
    withRow('f_sex')
    ops.push({ op: 'eq', column: 'f_sex.sex', value: f.sex })
  }
  if (f.lifeStage) {
    withRow('f_life')
    ops.push({ op: 'eq', column: 'f_life.life_stage', value: f.lifeStage })
  }
  if (f.annotationType === 'bbox') {
    withRow('f_box')
    ops.push({ op: 'notNull', column: 'f_box.bbox_x' })
  } else if (f.annotationType === 'whole') {
    const none = withoutRow('f_box')
    ops.push({ op: 'notNull', column: 'f_box.bbox_x' })
    none()
  }
  if (f.status === 'reviewed') {
    withRow('f_reviewed')
    ops.push({ op: 'in', column: 'f_reviewed.review_status', values: REVIEWED_STATES })
  } else if (f.status === 'ai') {
    withRow('f_ai')
    ops.push({ op: 'or', filters: AI_ROW, referencedTable: 'f_ai' })
    const none = withoutRow('f_reviewed')
    ops.push({ op: 'in', column: 'f_reviewed.review_status', values: REVIEWED_STATES })
    none()
  } else if (f.status === 'pending') {
    const none = withoutRow('f_labelled')
    ops.push({ op: 'or', filters: ANY_LABEL, referencedTable: 'f_labelled' })
    none()
  }

  // Photos with no capture time stay in, as they did when this ran in the browser.
  const from = DAY.test(f.dateFrom) ? `timestamp.gte.${f.dateFrom}` : null
  const to = DAY.test(f.dateTo) ? `timestamp.lte.${f.dateTo}T23:59:59` : null
  if (from && to) ops.push({ op: 'or', filters: `timestamp.is.null,and(${from},${to})` })
  else if (from || to) ops.push({ op: 'or', filters: `timestamp.is.null,${from ?? to}` })

  // media.local_hour is the capture hour in the deployment's zone, kept by the database
  // (ww-backend #335); null without a capture time, which matches neither.
  if (f.timeOfDay === 'day') {
    ops.push({ op: 'gte', column: 'local_hour', value: DAY_START_HOUR }, { op: 'lt', column: 'local_hour', value: NIGHT_START_HOUR })
  } else if (f.timeOfDay === 'night') {
    ops.push({ op: 'or', filters: `local_hour.gte.${NIGHT_START_HOUR},local_hour.lt.${DAY_START_HOUR}` })
  }

  return { embeds, ops }
}

/** The calls `applyFilterOps` makes; a supabase-js filter builder has all of them. */
export interface FilterableQuery<Q> {
  in(column: string, values: readonly string[]): Q
  eq(column: string, value: string): Q
  gte(column: string, value: number): Q
  lt(column: string, value: number): Q
  not(column: string, operator: string, value: null): Q
  is(column: string, value: null): Q
  or(filters: string, options?: { referencedTable?: string }): Q
}

export function applyFilterOps<Q extends FilterableQuery<Q>>(query: Q, ops: readonly FilterOp[]): Q {
  let q = query
  for (const o of ops) {
    if (o.op === 'in') q = q.in(o.column, o.values)
    else if (o.op === 'eq') q = q.eq(o.column, o.value)
    else if (o.op === 'gte') q = q.gte(o.column, o.value)
    else if (o.op === 'lt') q = q.lt(o.column, o.value)
    else if (o.op === 'notNull') q = q.not(o.column, 'is', null)
    else if (o.op === 'isNull') q = q.is(o.column, null)
    else q = o.referencedTable ? q.or(o.filters, { referencedTable: o.referencedTable }) : q.or(o.filters)
  }
  return q
}

/** Newest first, or "Needs review first": the active-learning score, highest first. */
export type GridSort = 'newest' | 'review'

/** One `.order()` call on the media query. */
export interface OrderSpec {
  column: string
  ascending: boolean
  nullsFirst?: boolean
}

export interface SortPlan {
  /** Extra select entries the order reads. */
  embeds: string[]
  orders: OrderSpec[]
}

const NEWEST: OrderSpec = { column: 'timestamp', ascending: false, nullsFirst: false }
/** Last, so photos that tie keep one order and no page repeats or skips one. */
const BY_ID: OrderSpec = { column: 'id', ascending: true }

/**
 * The media query's order. The score sits on the photo's `media_embeddings` row, one-to-one
 * since ww-backend #337, so PostgREST orders photos by it; a photo with no row (not embedded
 * yet) has no score and comes after every scored one, newest first.
 */
export function gridSortPlan(sort: GridSort): SortPlan {
  if (sort === 'review') {
    return {
      embeds: ['media_embeddings(active_learning_score)'],
      orders: [{ column: 'media_embeddings(active_learning_score)', ascending: false, nullsFirst: false }, NEWEST, BY_ID],
    }
  }
  return { embeds: [], orders: [NEWEST, BY_ID] }
}

export function applyOrder<Q extends { order(column: string, options: Omit<OrderSpec, 'column'>): Q }>(
  query: Q, orders: readonly OrderSpec[],
): Q {
  let q = query
  for (const { column, ...options } of orders) q = q.order(column, options)
  return q
}

/** The filters set in the Advanced filters dialog (everything but deployment and species). */
export function hasAdvancedFilters(f: GridFilters): boolean {
  return !!(f.status || f.annotator || f.model || f.annotationType || f.sex || f.lifeStage || f.dateFrom || f.dateTo || f.timeOfDay)
}

/** Any filter beyond the deployment choice. */
export function hasAnyFilter(f: GridFilters): boolean {
  return f.species.length > 0 || hasAdvancedFilters(f)
}

/** A stable key for a query: changes exactly when the rows it can return change. */
export function gridQueryKey(deploymentIds: readonly string[], f: GridFilters): string {
  return JSON.stringify([deploymentIds, { ...f, species: f.species.toSorted() }])
}
