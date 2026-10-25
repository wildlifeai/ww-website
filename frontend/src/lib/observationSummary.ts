// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Insights > Reports: the observations, counted once into the small tables each counter and
// chart draws (UI audit 04 #8). Vega gets a few rows per chart instead of every observation.

export interface SummaryObservation {
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  /** When the photo was taken (`media.timestamp`); null when there is none. */
  captured_at: string | null
}

export interface CountRow {
  key: string
  label: string
  count: number
}

export interface ObservationSummary {
  total: number
  uniqueSpecies: number
  /** Deployments with at least one observation that is not blank. */
  deploymentsWithDetections: number
  animals: number
  /** Most first. Every named species, '(unidentified)' included. */
  species: CountRow[]
  /** Observation type, 'unknown' when empty. */
  types: CountRow[]
  /** One row per deployment, labelled by `deploymentLabels`. */
  deployments: CountRow[]
}

const UNIDENTIFIED = '(unidentified)'

function sortedRows(counts: Map<string, number>, labelOf: (key: string) => string): CountRow[] {
  return [...counts]
    .map(([key, count]) => ({ key, label: labelOf(key), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function bump(counts: Map<string, number>, key: string) {
  counts.set(key, (counts.get(key) ?? 0) + 1)
}

/** One pass over the observations. A deployment without a label keeps its id prefix. */
export function summariseObservations(
  observations: SummaryObservation[],
  deploymentLabels: ReadonlyMap<string, string> = new Map(),
): ObservationSummary {
  const species = new Map<string, number>()
  const types = new Map<string, number>()
  const deployments = new Map<string, number>()
  const detected = new Set<string>()
  let animals = 0
  for (const o of observations) {
    if (o.scientific_name) bump(species, o.scientific_name)
    const type = o.observation_type || 'unknown'
    bump(types, type)
    bump(deployments, o.deployment_id)
    if (type !== 'blank') detected.add(o.deployment_id)
    if (type === 'animal') animals++
  }
  return {
    total: observations.length,
    uniqueSpecies: [...species.keys()].filter(s => s !== UNIDENTIFIED).length,
    deploymentsWithDetections: detected.size,
    animals,
    species: sortedRows(species, k => k),
    types: sortedRows(types, k => k),
    deployments: sortedRows(deployments, k => deploymentLabels.get(k) ?? k.slice(0, 8)),
  }
}

/** The first `max` rows and how many were left out. */
export function capRows<T>(rows: T[], max: number): { shown: T[]; more: number } {
  return { shown: rows.slice(0, max), more: Math.max(0, rows.length - max) }
}

// ── Detections per day by species (one deployment) ─────────────────────────

export interface DayRow {
  /** Calendar day in the deployment's zone, YYYY-MM-DD. */
  day: string
  species: string
  count: number
}

export const OTHER_SPECIES = 'Other species'

const dayFormatters = new Map<string, Intl.DateTimeFormat>()

/** YYYY-MM-DD in `timeZone` (the browser's when empty or unknown). */
export function dayKey(iso: string, timeZone?: string | null): string | null {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  const zone = timeZone || ''
  let f = dayFormatters.get(zone)
  if (!f) {
    try {
      f = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', ...(zone ? { timeZone: zone } : {}) })
    } catch {
      f = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' })
    }
    dayFormatters.set(zone, f)
  }
  const parts = f.formatToParts(d)
  const part = (type: string) => parts.find(p => p.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
}

/**
 * Named observations counted by the day their photo was taken (`captured_at`), in the
 * deployment's zone, the browser's when it has none. Not `created_at`: that is when the AI or a
 * person labelled the photo, so an upload or a re-run would put every photo on one day. Those
 * without a capture time are left out and counted in `undated`, so the chart's sum plus
 * `undated` is every named observation. The `maxSpecies` most frequent species keep their name;
 * the rest are summed into "Other species", so the colours stay readable.
 */
export function detectionsPerDay(
  observations: SummaryObservation[], timeZone?: string | null, maxSpecies = 8,
): { rows: DayRow[]; species: string[]; undated: number } {
  const dated: { day: string; species: string }[] = []
  let undated = 0
  for (const o of observations) {
    if (!o.scientific_name) continue
    const day = o.captured_at ? dayKey(o.captured_at, timeZone) : null
    if (day) dated.push({ day, species: o.scientific_name })
    else undated++
  }

  const totals = new Map<string, number>()
  for (const d of dated) bump(totals, d.species)
  const ranked = sortedRows(totals, k => k).map(r => r.key)
  const named = new Set(ranked.slice(0, maxSpecies))

  const cells = new Map<string, DayRow>()
  for (const d of dated) {
    const species = named.has(d.species) ? d.species : OTHER_SPECIES
    const key = `${d.day}|${species}`
    const cell = cells.get(key)
    if (cell) cell.count++
    else cells.set(key, { day: d.day, species, count: 1 })
  }
  const species = ranked.slice(0, maxSpecies)
  if (ranked.length > maxSpecies) species.push(OTHER_SPECIES)
  const rows = [...cells.values()].sort((a, b) => a.day.localeCompare(b.day) || a.species.localeCompare(b.species))
  return { rows, species, undated }
}

/** The per-day table as CSV: one row per day, one column per species, as Reporting exported it. */
export function perDayCsv(rows: DayRow[], species: string[]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const byDay = new Map<string, Map<string, number>>()
  for (const r of rows) {
    const day = byDay.get(r.day) ?? new Map<string, number>()
    day.set(r.species, (day.get(r.species) ?? 0) + r.count)
    byDay.set(r.day, day)
  }
  const lines = [...byDay]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, counts]) => [day, ...species.map(s => String(counts.get(s) ?? 0))].join(','))
  return [['date', ...species].map(esc).join(','), ...lines].join('\n')
}
