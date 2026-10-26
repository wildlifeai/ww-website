// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Insights > Reports: the database's counts (ww-backend #334), summed into the small tables each
// counter and chart draws (UI audit 04 #8). Vega gets a few rows per chart instead of every
// observation.

/** Live observations of one deployment with one species and type (`insights_observation_counts`). */
export interface ObservationCount {
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  n: number
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

function bump(counts: Map<string, number>, key: string, n = 1) {
  counts.set(key, (counts.get(key) ?? 0) + n)
}

/** One pass over the counted rows. A deployment without a label keeps its id prefix. */
export function summariseObservations(
  counts: ObservationCount[],
  deploymentLabels: ReadonlyMap<string, string> = new Map(),
): ObservationSummary {
  const species = new Map<string, number>()
  const types = new Map<string, number>()
  const deployments = new Map<string, number>()
  const detected = new Set<string>()
  let total = 0
  let animals = 0
  for (const o of counts) {
    total += o.n
    if (o.scientific_name) bump(species, o.scientific_name, o.n)
    const type = o.observation_type || 'unknown'
    bump(types, type, o.n)
    bump(deployments, o.deployment_id, o.n)
    if (type !== 'blank') detected.add(o.deployment_id)
    if (type === 'animal') animals += o.n
  }
  return {
    total,
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

/** The rows of `deploymentIds` that name one of `species`; an empty list keeps every value. */
export function filterCounts<T extends { deployment_id: string; scientific_name: string | null }>(
  rows: T[], deploymentIds: readonly string[], species: readonly string[],
): T[] {
  if (deploymentIds.length === 0 && species.length === 0) return rows
  const deps = new Set(deploymentIds)
  const names = new Set(species)
  return rows.filter(r =>
    (deps.size === 0 || deps.has(r.deployment_id)) && (names.size === 0 || names.has(r.scientific_name ?? '')))
}

// ── Detections per day by species (one deployment) ─────────────────────────

export interface DayRow {
  /** Calendar day in the deployment's zone, YYYY-MM-DD. */
  day: string
  species: string
  count: number
}

export const OTHER_SPECIES = 'Other species'

/** Named live observations of a deployment per capture day and species (`deployment_daily_counts`). */
export interface DailyCount {
  deployment_id: string
  /** The photo's capture date in the deployment's zone, YYYY-MM-DD; null with no capture time. */
  day: string | null
  scientific_name: string | null
  n: number
}

/**
 * Named observations by the day their photo was taken, as the database dated them in the
 * deployment's zone, the browser's when it has none. Not `created_at`: that is when the AI or a
 * person labelled the photo, so an upload or a re-run would put every photo on one day. Those
 * without a capture time are left out and counted in `undated`, so the chart's sum plus
 * `undated` is every named observation. The `maxSpecies` most frequent species keep their name;
 * the rest are summed into "Other species", so the colours stay readable.
 */
export function detectionsPerDay(
  counts: DailyCount[], maxSpecies = 8,
): { rows: DayRow[]; species: string[]; undated: number } {
  const dated: { day: string; species: string; n: number }[] = []
  let undated = 0
  for (const c of counts) {
    if (!c.scientific_name) continue
    if (c.day) dated.push({ day: c.day, species: c.scientific_name, n: c.n })
    else undated += c.n
  }

  const totals = new Map<string, number>()
  for (const d of dated) bump(totals, d.species, d.n)
  const ranked = sortedRows(totals, k => k).map(r => r.key)
  const named = new Set(ranked.slice(0, maxSpecies))

  const cells = new Map<string, DayRow>()
  for (const d of dated) {
    const label = named.has(d.species) ? d.species : OTHER_SPECIES
    const key = `${d.day}|${label}`
    const cell = cells.get(key)
    if (cell) cell.count += d.n
    else cells.set(key, { day: d.day, species: label, count: d.n })
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
