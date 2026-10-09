// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Edit a deployment's location (#288). Location lives on each deployments row; there is no
// locations table. The save goes through PATCH /api/deployments/{id}/location, which writes as
// the signed-in user (RLS: the creator while still a project member, or a project admin),
// recomputes the time zone from the coordinates and refuses a 0-row update with a 403.
import type { SupabaseClient } from '@supabase/supabase-js'
import { apiClient } from './apiClient'

export interface DeploymentLocation {
  id: string
  location_name: string
  location_description: string | null
  latitude: number | null
  longitude: number | null
  altitude: number | null
  accuracy: number | null
  timezone: string | null
}

export const LOCATION_COLUMNS =
  'id, location_name, location_description, latitude, longitude, altitude, accuracy, timezone'

/** The form holds what the user typed; numbers are parsed only to validate and save. */
export interface LocationForm {
  name: string
  description: string
  latitude: string
  longitude: string
  altitude: string
  accuracy: string
}

export type LocationErrors = Partial<Record<keyof LocationForm, string>>

/** What the endpoint takes: every field, so a blank clears the column. */
export interface LocationPayload {
  location_name: string
  location_description: string | null
  latitude: number | null
  longitude: number | null
  altitude: number | null
  accuracy: number | null
}

const text = (n: number | null) => (n == null ? '' : String(n))

export function formFromLocation(loc: DeploymentLocation): LocationForm {
  return {
    name: loc.location_name ?? '',
    description: loc.location_description ?? '',
    latitude: text(loc.latitude),
    longitude: text(loc.longitude),
    altitude: text(loc.altitude),
    accuracy: text(loc.accuracy),
  }
}

/** Blank is null, a number is that number, anything else is NaN. */
export function parseOptionalNumber(raw: string): number | null {
  const s = raw.trim()
  if (s === '') return null
  const n = Number(s)
  return Number.isFinite(n) ? n : NaN
}

export function validateLocation(form: LocationForm): LocationErrors {
  const errors: LocationErrors = {}
  if (!form.name.trim()) errors.name = 'A location name is required.'

  const lat = parseOptionalNumber(form.latitude)
  const lon = parseOptionalNumber(form.longitude)
  if (Number.isNaN(lat) || (lat != null && (lat < -90 || lat > 90))) errors.latitude = 'Latitude is a number from -90 to 90.'
  if (Number.isNaN(lon) || (lon != null && (lon < -180 || lon > 180))) errors.longitude = 'Longitude is a number from -180 to 180.'
  if (!errors.latitude && !errors.longitude && (lat == null) !== (lon == null)) {
    errors[lat == null ? 'latitude' : 'longitude'] = 'Give both latitude and longitude, or neither.'
  }

  if (Number.isNaN(parseOptionalNumber(form.altitude))) errors.altitude = 'Altitude is a number of metres.'
  const acc = parseOptionalNumber(form.accuracy)
  if (Number.isNaN(acc) || (acc != null && acc < 0)) errors.accuracy = 'Accuracy is a number of metres, 0 or more.'
  return errors
}

/** True when the form's point differs from the stored one. */
export function coordinatesMoved(form: LocationForm, stored: DeploymentLocation): boolean {
  return parseOptionalNumber(form.latitude) !== stored.latitude
    || parseOptionalNumber(form.longitude) !== stored.longitude
}

/**
 * The payload for a valid form. The stored accuracy described the old point, so when the point
 * moves and the accuracy was left as it was, it is cleared rather than carried over.
 */
export function buildLocationPayload(form: LocationForm, stored: DeploymentLocation): LocationPayload {
  const accuracy = parseOptionalNumber(form.accuracy)
  const staleAccuracy = coordinatesMoved(form, stored) && accuracy === stored.accuracy
  return {
    location_name: form.name.trim(),
    location_description: form.description.trim() || null,
    latitude: parseOptionalNumber(form.latitude),
    longitude: parseOptionalNumber(form.longitude),
    altitude: parseOptionalNumber(form.altitude),
    accuracy: staleAccuracy ? null : accuracy,
  }
}

/** Read the location as the signed-in user; null when they cannot see the deployment. */
export async function fetchDeploymentLocation(db: SupabaseClient, id: string): Promise<DeploymentLocation | null> {
  const { data, error } = await db.from('deployments').select(LOCATION_COLUMNS).eq('id', id).is('deleted_at', null).maybeSingle()
  if (error) throw new Error(error.message)
  return data as DeploymentLocation | null
}

export async function saveDeploymentLocation(id: string, payload: LocationPayload): Promise<void> {
  await apiClient.patch(`/api/deployments/${id}/location`, payload)
}
