// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Time formatting helpers for rendering capture times in the *deployment's* local
// timezone rather than the viewer's browser timezone.
//
// media.timestamp is stored as a UTC instant. Each deployment carries an IANA
// timezone (deployments.timezone, e.g. "Pacific/Auckland"), resolved from its GPS
// coordinates. To show "the time where the photo was taken", we format that UTC
// instant in the deployment's zone. When the timezone is unknown (null, or not yet
// backfilled), we fall back to the browser's local zone — the previous behaviour.

/** A deployment timezone, or null/undefined when unknown (→ browser-local fallback). */
export type DeploymentTimezone = string | null | undefined

/** True if `tz` is a usable IANA zone the runtime's Intl supports. */
function isValidTz(tz: DeploymentTimezone): tz is string {
  if (!tz) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return true
  } catch {
    return false
  }
}

/**
 * Format a UTC timestamp as a full date+time in the deployment's local zone,
 * with a short zone label (e.g. "9 Jun 2026, 10:44 NZST"). Falls back to the
 * browser zone when `tz` is unknown.
 */
export function formatCaptureTime(iso: string | null, tz: DeploymentTimezone): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const opts: Intl.DateTimeFormatOptions = {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZoneName: 'short',
  }
  if (isValidTz(tz)) opts.timeZone = tz
  try {
    return new Intl.DateTimeFormat(undefined, opts).format(d)
  } catch {
    return d.toLocaleString()
  }
}

/**
 * Day is 06:00 to 18:00 in the deployment's local time, night the rest. The review grid filters
 * on these hours (`media.local_hour`, lib/gridFilters).
 */
export const DAY_START_HOUR = 6
export const NIGHT_START_HOUR = 18
