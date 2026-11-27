// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Label checks (#448): flags from the Wildlife Brain's fingerprints on labels that are probably
// wrong, for a second look (design: documentation/development reports/2026-11_label-checks.md).
// The backend computes them into ww-backend's `label_checks` (domain/label_check_runs.py); the
// grid reads the open ones, words them, orders them (decision 13), lists the confused species
// pairs (decision 8) and filters them by the sensitivity slider (decision 14).
import { isHumanReviewed, type ObservationStatusFields } from './observations'

export type LabelCheckKind = 'odd_one_out' | 'look_alike' | 'more_specific'

export interface LabelCheck {
  id: string
  observation_id: string
  project_id: string
  kind: LabelCheckKind
  /** 0.5 (just flagged) to 1 (strongest), for every kind. */
  score: number
  other_scientific_name: string | null
  /** What the species' reference was built from: confirmed labels, or AI labels (ranked lower). */
  reference: 'confirmed' | 'ai'
  label_scientific_name: string | null
  media_id: string
  /** The flagged label is a person's (or one a person confirmed), not the AI's alone. */
  confirmed: boolean
}

/** The open flags' columns, with the flagged label's photo. */
export const LABEL_CHECK_SELECT =
  'id, observation_id, project_id, kind, score, other_scientific_name, reference, label_scientific_name, ' +
  'observations!inner(media_id, review_status, source_type, classification_method)'

export interface LabelCheckRow extends Omit<LabelCheck, 'media_id' | 'confirmed'> {
  observations: (ObservationStatusFields & { media_id: string | null }) | null
}

/** A row as PostgREST returns it, or null when its label has no photo. */
export function toLabelCheck(row: LabelCheckRow): LabelCheck | null {
  const { observations, ...rest } = row
  return observations?.media_id ? { ...rest, media_id: observations.media_id, confirmed: isHumanReviewed(observations) } : null
}

/** The slider's start, 0 (strongest only) to 100 (every flag); decision 15's feedback sets it. */
export const DEFAULT_SENSITIVITY = 80

/** The lowest score shown at a sensitivity: 1 at 0, 0.5 (every flag) at 100. */
export function minScore(sensitivity: number): number {
  return 1 - Math.min(100, Math.max(0, sensitivity)) / 200
}

/** The flagged label's words on a card. */
export function reasonText(c: Pick<LabelCheck, 'kind' | 'other_scientific_name' | 'label_scientific_name'>): string {
  const label = c.label_scientific_name ?? 'this species'
  if (c.kind === 'look_alike') return `Looks like ${c.other_scientific_name}, labelled ${label}`
  if (c.kind === 'more_specific') return `Could be ${c.other_scientific_name}`
  return `Unlike other ${label}`
}

const nameKey = (name: string | null | undefined) => (name ?? '').trim().split(/\s+/).join(' ').toLowerCase()

/**
 * Whether a flag still applies to its photo: its label is the one the photo shows. A relabel
 * clears the flag at once (decision 11); the next recompute records it as relabelled.
 */
export function stillLabelled(c: Pick<LabelCheck, 'label_scientific_name'>, currentName: string | null | undefined): boolean {
  return nameKey(c.label_scientific_name) === nameKey(currentName)
}

/**
 * The review order (decisions 13 and 17): confirmed labels first, then AI labels; within each,
 * "could be more specific" last, AI-reference checks after confirmed-reference ones, strongest first.
 */
export function orderChecks(checks: readonly LabelCheck[]): LabelCheck[] {
  const rank = (c: LabelCheck) => [c.confirmed ? 0 : 1, c.kind === 'more_specific' ? 1 : 0, c.reference === 'ai' ? 1 : 0, -c.score]
  return checks.toSorted((a, b) => {
    const ra = rank(a)
    const rb = rank(b)
    for (let i = 0; i < ra.length; i++) if (ra[i] !== rb[i]) return ra[i] - rb[i]
    return a.id.localeCompare(b.id)
  })
}

export interface ConfusedPair {
  /** The two species, alphabetical. */
  species: [string, string]
  count: number
}

/** The species pairs look-alike flags most often join, most first (decision 8). */
export function confusedPairs(checks: readonly LabelCheck[], limit = 8): ConfusedPair[] {
  const counts = new Map<string, ConfusedPair>()
  for (const c of checks) {
    if (c.kind !== 'look_alike' || !c.label_scientific_name || !c.other_scientific_name) continue
    const species = [c.label_scientific_name, c.other_scientific_name].toSorted() as [string, string]
    const key = species.join('|')
    const pair = counts.get(key) ?? { species, count: 0 }
    pair.count += 1
    counts.set(key, pair)
  }
  return [...counts.values()].toSorted((a, b) => b.count - a.count || a.species.join().localeCompare(b.species.join())).slice(0, limit)
}

/** A look-alike flag between the pair's two species, either way round. */
export function inPair(c: LabelCheck, pair: readonly [string, string]): boolean {
  if (c.kind !== 'look_alike') return false
  const species = [nameKey(c.label_scientific_name), nameKey(c.other_scientific_name)]
  return species.includes(nameKey(pair[0])) && species.includes(nameKey(pair[1])) && nameKey(pair[0]) !== nameKey(pair[1])
}

/** Each photo's strongest flag. */
export function strongestByMedia(checks: readonly LabelCheck[]): Map<string, LabelCheck> {
  const out = new Map<string, LabelCheck>()
  for (const c of checks) {
    const held = out.get(c.media_id)
    if (!held || c.score > held.score) out.set(c.media_id, c)
  }
  return out
}
