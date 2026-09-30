// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// trainingDataset: pure helpers for the "Create species ID model" action.
// Mirrors the backend rules in backend/app/domain/training.py so the modal can
// preview what a selection will turn into before anything is submitted:
//   - observations the camera itself produced (ai_origin 'edge') never count
//   - when an image has a human observation, only human observations count
//   - a blank observation is one background image
// The backend recomputes everything; this is a preview, not the contract.

export interface ObservationLike {
  id: string
  observation_type: string | null
  scientific_name: string | null
  vernacular_name?: string | null
  taxon_id?: string | null
  ai_origin?: string | null
  source_type?: string | null
  reviewer_id?: string | null
  annotator_id?: string | null
}

export interface MediaLike {
  id: string
  deployment_id: string
  observations: ObservationLike[]
}

export interface ClassCandidate {
  /** Observation scientific_name the class is built from (matching key). */
  scientific_name: string
  vernacular_name: string | null
  taxon_id: string | null
  /** Number of usable observations (= training samples) for this species. */
  count: number
  /** How many of those are human-reviewed. */
  human: number
}

export interface SelectionSummary {
  classes: ClassCandidate[]
  blanks: number
  unlabelled: number
  edgeOnly: number
}

const isHuman = (o: ObservationLike) => !!(o.reviewer_id || o.annotator_id || o.source_type === 'human')

/** Device/Edge-Impulse-safe label: lowercase letters, digits, spaces, _ and -; max 32. */
export function sanitizeLabel(raw: string): string {
  const s = (raw || '').trim().toLowerCase().replace(/[^a-z0-9 _-]+/g, ' ').replace(/\s+/g, ' ').replace(/^[ _-]+|[ _-]+$/g, '')
  return s.slice(0, 32).replace(/[ _-]+$/g, '')
}

/**
 * Background label that sorts before the targets. Edge Impulse orders classes
 * alphabetically and the camera treats class index 1 as the target for a
 * two-class model, so "not gecko" (which sorts after "gecko") is avoided.
 */
export function defaultBackgroundLabel(targetLabels: string[]): string {
  const targets = targetLabels.filter(Boolean).slice().sort()
  if (targets.length === 0) return 'background'
  const first = targets[0]
  const candidates = (targetLabels.length === 1 ? [`not ${first}`] : []).concat(['other', 'background', '_background'])
  for (const c of candidates) if (c < first && !targetLabels.includes(c)) return c
  return '_background'
}

/** A readable default device label for a species: vernacular name if known, else the scientific name. */
export function defaultLabelFor(c: Pick<ClassCandidate, 'scientific_name' | 'vernacular_name'>): string {
  return sanitizeLabel(c.vernacular_name || c.scientific_name)
}

/** Group a selection into class candidates, the way the backend will. */
export function summarizeSelection(media: MediaLike[]): SelectionSummary {
  const classes = new Map<string, ClassCandidate>()
  let blanks = 0, unlabelled = 0, edgeOnly = 0
  for (const m of media) {
    const usable = (m.observations || []).filter(o => o.ai_origin !== 'edge')
    if (usable.length === 0) {
      if ((m.observations || []).length > 0) edgeOnly++
      else unlabelled++
      continue
    }
    const humans = usable.filter(isHuman)
    const chosen = humans.length > 0 ? humans : usable
    let blankCounted = false
    for (const o of chosen) {
      if (o.observation_type === 'blank') {
        if (!blankCounted) { blanks++; blankCounted = true }
        continue
      }
      const name = (o.scientific_name || '').trim()
      if (!name) continue
      const key = name.toLowerCase()
      const entry = classes.get(key) ?? { scientific_name: name, vernacular_name: o.vernacular_name ?? null, taxon_id: o.taxon_id ?? null, count: 0, human: 0 }
      entry.count++
      if (isHuman(o)) entry.human++
      if (!entry.vernacular_name && o.vernacular_name) entry.vernacular_name = o.vernacular_name
      if (!entry.taxon_id && o.taxon_id) entry.taxon_id = o.taxon_id
      classes.set(key, entry)
    }
  }
  return {
    classes: [...classes.values()].sort((a, b) => b.count - a.count || a.scientific_name.localeCompare(b.scientific_name)),
    blanks,
    unlabelled,
    edgeOnly,
  }
}

/** Images that will land in the background class for a given target set. */
export function backgroundCount(summary: SelectionSummary, targetNames: Set<string>): number {
  const others = summary.classes.filter(c => !targetNames.has(c.scientific_name.toLowerCase())).reduce((n, c) => n + c.count, 0)
  return summary.blanks + others
}
