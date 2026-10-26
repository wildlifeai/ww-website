// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The GBF contribution report (#372): the shape GET /api/reports/gbf returns (#371), its default
// period, and the CSV download. The figures and their definitions are the backend's
// (domain/gbf_report.py); the guide explains them (/guides/gbf-contribution-report).

export const GBF_GUIDE = '/guides/gbf-contribution-report'
export const GBF_CAVEAT = "Site-level evidence towards the targets, not the GBF's national indicators."

export interface GbfSpecies {
  taxon_id: string
  scientific_name: string | null
  common_name: string | null
  conservation_status: string | null
  invasive: boolean
  detections: number
  /** Detections per 100 camera-nights; null with no camera-nights. */
  rate: number | null
  sites_with: number
  monthly: { month: string; detections: number; rate: number | null }[]
  first_detections: { deployment_id: string; deployment_name: string | null; date: string }[]
}

export interface GbfSpeciesTarget {
  sites_monitored: number
  species_recorded: number
  species: GbfSpecies[]
}

/** Target 21: one project's GBIF dataset beside its reviewed detections in the period (#373). */
export interface GbfDataset {
  project_id: string
  project_name: string | null
  dataset_key: string | null
  /** not_published: no dataset linked; not_found: GBIF no longer has it; unavailable: GBIF did not answer. */
  status: 'published' | 'not_published' | 'not_found' | 'unavailable'
  title: string | null
  doi: string | null
  published_records: number | null
  last_published: string | null
  reviewed_detections: number
}

export interface GbfReport {
  method_version: string
  period: { from: string; to: string }
  projects: { id: string; name: string | null }[]
  camera_nights_by_month: { month: string; camera_nights: number }[]
  target_6: GbfSpeciesTarget
  target_4: GbfSpeciesTarget
  target_20: {
    cameras: number
    sites: number
    camera_nights: number
    photos: number
    photos_with_ai: number
    photos_reviewed: number
  }
  target_21: { reviewed_detections: number; datasets: GbfDataset[] }
  target_22: { reviewers: number }
}

/** The target's text on cbd.int. */
export const targetUrl = (target: number) => `https://www.cbd.int/gbf/targets/${target}`

const day = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** The last 12 months, today included: a year ago tomorrow to today. */
export function defaultPeriod(today = new Date()): { from: string; to: string } {
  const from = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate() + 1)
  return { from: day(from), to: day(today) }
}

/** The message to show, or null when the period can be asked for. */
export function periodProblem(from: string, to: string): string | null {
  if (!from || !to) return 'Pick a start and an end date.'
  return from > to ? 'The start date is after the end date.' : null
}

export function gbfReportPath(projectIds: string[], from: string, to: string): string {
  const q = new URLSearchParams({ project_ids: projectIds.join(','), from, to })
  return `/api/reports/gbf?${q.toString()}`
}

export async function fetchGbfReport(
  get: (path: string) => Promise<unknown>,
  projectIds: string[],
  from: string,
  to: string,
): Promise<GbfReport> {
  const res = await get(gbfReportPath(projectIds, from, to)) as { data?: GbfReport }
  if (!res?.data) throw new Error('The report could not be calculated.')
  return res.data
}

/** "Ship rat (Rattus rattus)", or the one name there is. */
export function speciesLabel(s: Pick<GbfSpecies, 'common_name' | 'scientific_name'>): string {
  if (s.common_name && s.scientific_name) return `${s.common_name} (${s.scientific_name})`
  return s.common_name || s.scientific_name || 'Unnamed species'
}

export function gbfFilename(report: GbfReport, ext: 'csv' | 'json'): string {
  return `gbf-report-${report.period.from}-to-${report.period.to}.${ext}`
}

type Cell = string | number | null

function csvCell(v: Cell): string {
  if (v === null) return ''
  if (typeof v === 'number') return String(v)
  // A leading = + - or @ would run as a formula in a spreadsheet.
  const text = /^[=+\-@]/.test(v) ? `'${v}` : v
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function speciesRows(target: number, t: GbfSpeciesTarget): Cell[][] {
  const rows: Cell[][] = [
    [target, null, 'sites_monitored', null, t.sites_monitored],
    [target, null, 'species_recorded', null, t.species_recorded],
  ]
  for (const s of t.species) {
    const name = s.scientific_name
    rows.push(
      [target, name, 'detections', null, s.detections],
      [target, name, 'rate_per_100_camera_nights', null, s.rate],
      [target, name, 'sites_with', null, s.sites_with],
      [target, name, 'first_detections', null, s.first_detections.length],
    )
    for (const m of s.monthly) {
      rows.push([target, name, 'detections', m.month, m.detections], [target, name, 'rate_per_100_camera_nights', m.month, m.rate])
    }
  }
  return rows
}

function openDataRows(datasets: GbfDataset[]): Cell[][] {
  return datasets.flatMap((d): Cell[][] => {
    const name = d.project_name ?? d.project_id
    return [
      [21, name, 'status', null, d.status],
      [21, name, 'published_records', null, d.published_records],
      [21, name, 'reviewed_detections', null, d.reviewed_detections],
      [21, name, 'last_published', null, d.last_published],
      [21, name, 'doi', null, d.doi],
    ]
  })
}

/**
 * The report as CSV: the period and projects first, then one figure per row, as
 * `target, subject, metric, month, value`. The subject is the species' scientific name, or the
 * project for Target 21, and empty for a figure about the whole report; the month is empty for a
 * figure over the whole period.
 */
export function gbfCsv(report: GbfReport): string {
  const e = report.target_20
  const rows: Cell[][] = [
    ['report', 'GBF contribution'],
    ['period_from', report.period.from],
    ['period_to', report.period.to],
    ['projects', report.projects.map(p => p.name ?? p.id).join('; ')],
    ['method_version', report.method_version],
    ['note', GBF_CAVEAT],
    [],
    ['target', 'subject', 'metric', 'month', 'value'],
    ...speciesRows(6, report.target_6),
    ...speciesRows(4, report.target_4),
    [20, null, 'cameras', null, e.cameras],
    [20, null, 'sites', null, e.sites],
    [20, null, 'camera_nights', null, e.camera_nights],
    ...report.camera_nights_by_month.map((m): Cell[] => [20, null, 'camera_nights', m.month, m.camera_nights]),
    [20, null, 'photos', null, e.photos],
    [20, null, 'photos_with_ai', null, e.photos_with_ai],
    [20, null, 'photos_reviewed', null, e.photos_reviewed],
    ...openDataRows(report.target_21.datasets),
    [22, null, 'reviewers', null, report.target_22.reviewers],
  ]
  return rows.map(r => r.map(csvCell).join(',')).join('\n') + '\n'
}
