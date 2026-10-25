// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The organisations the user manages (`/api/models/managed-orgs`), and an organisation's API keys.
// The query key and the data shape match the top bar's (App.tsx), so they share one request.
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { listApiKeys } from '../lib/apiKeys'
import { useAuth } from './useAuth'

export interface ManagedOrg { id: string; name: string }

const NONE: ManagedOrg[] = []

export function useManagedOrgs(): ManagedOrg[] {
  const { user } = useAuth()
  const { data } = useQuery<ManagedOrg[]>({
    queryKey: ['managedOrgs', user?.id],
    queryFn: async () => {
      try {
        const res = await apiClient.get('/api/models/managed-orgs') as { data?: ManagedOrg[] }
        return res?.data || []
      } catch { return [] }
    },
    enabled: !!user,
  })
  return data ?? NONE
}

/** An organisation's keys; the error is FEATURE_DISABLED while the public API is off. */
export function useApiKeys(orgId: string) {
  return useQuery({
    queryKey: ['apiKeys', orgId],
    queryFn: () => listApiKeys(orgId),
    enabled: !!orgId,
    retry: false,
  })
}
