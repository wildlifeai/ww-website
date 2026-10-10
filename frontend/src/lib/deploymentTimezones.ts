// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Fill a deployment's time zone the first time the website shows it (#309).
//
// The app creates deployments without a timezone, so their capture times would show in UTC.
// POST /api/deployments/fill-timezones stores the zone of each listed deployment that has
// coordinates and none, and returns the zones it stored; the page merges them in.

type Post = (path: string, body: unknown) => Promise<unknown>

export type TimezoneRow = {
  id: string
  timezone?: string | null
  latitude?: number | null
  longitude?: number | null
}

// The endpoint's limit per request.
export const FILL_BATCH = 500

/** Deployments read with an empty zone, except those known to have no coordinates. A row read
 * without the timezone column (undefined) is not asked about. */
export function missingTimezoneIds(rows: TimezoneRow[]): string[] {
  return rows
    .filter(r => r.timezone === null && r.latitude !== null && r.longitude !== null)
    .map(r => r.id)
}

export async function fillTimezones(post: Post, ids: string[]): Promise<Record<string, string>> {
  const batches: string[][] = []
  for (let i = 0; i < ids.length; i += FILL_BATCH) batches.push(ids.slice(i, i + FILL_BATCH))
  const answers = await Promise.all(batches.map(batch =>
    post('/api/deployments/fill-timezones', { deployment_ids: batch }) as Promise<{ data?: Record<string, string> }>))
  return Object.assign({}, ...answers.map(res => res?.data ?? {}))
}

/** The rows with the filled zones merged in; the same array when none applies. */
export function withTimezones<T extends TimezoneRow>(rows: T[], filled: Record<string, string>): T[] {
  if (!rows.some(r => !r.timezone && filled[r.id])) return rows
  return rows.map(r => (!r.timezone && filled[r.id] ? { ...r, timezone: filled[r.id] } : r))
}
