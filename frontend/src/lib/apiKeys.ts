// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Organisation API keys for the public API (#307), through the backend's /api/v1/api-keys.
// Only an organisation's managers may create, list and revoke its keys; the backend checks.
// What the keys are for: documentation/resources/api-reference.md, "Public Data API (v1)".

import { ApiError, apiClient } from './apiClient'

// The backend's VALID_SCOPES (backend/app/services/api_key.py).
export const API_KEY_SCOPES = [
  'deployments:read',
  'devices:read',
  'telemetry:read',
  'observations:read',
  'export:camtrapdp',
  'models:read',
] as const

export interface ApiKeyInfo {
  id: string
  name: string
  key_prefix: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  created_at: string | null
}

export interface CreatedApiKey extends Omit<ApiKeyInfo, 'last_used_at'> {
  /** The raw key. Shown once, never retrievable again. */
  key: string
}

/** The backend answers FEATURE_DISABLED while FF_PUBLIC_API_ENABLED is off. */
export function isPublicApiDisabled(err: unknown): boolean {
  return err instanceof ApiError && err.code === 'FEATURE_DISABLED'
}

/** The end of the picked day (`YYYY-MM-DD`) in the viewer's time zone, as ISO, or null for none. */
export function expiryFromDate(day: string): string | null {
  if (!day) return null
  const end = new Date(`${day}T23:59:59.999`)
  return Number.isNaN(end.getTime()) ? null : end.toISOString()
}

const orgParam = (orgId: string) => `organisation_id=${encodeURIComponent(orgId)}`

export async function listApiKeys(orgId: string): Promise<ApiKeyInfo[]> {
  const res = await apiClient.get(`/api/v1/api-keys?${orgParam(orgId)}`) as { data?: ApiKeyInfo[] }
  return res?.data ?? []
}

export async function createApiKey(
  orgId: string, name: string, scopes: string[], expiresAt: string | null,
): Promise<CreatedApiKey> {
  const res = await apiClient.post('/api/v1/api-keys', {
    organisation_id: orgId, name: name.trim(), scopes, expires_at: expiresAt,
  }) as { data: CreatedApiKey }
  return res.data
}

export async function revokeApiKey(orgId: string, keyId: string): Promise<void> {
  await apiClient.del(`/api/v1/api-keys/${encodeURIComponent(keyId)}?${orgParam(orgId)}`)
}
