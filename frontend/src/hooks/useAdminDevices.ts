// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useAdminDevices — every live device, for a ww_admin (GET /api/admin/devices, #343).
// Pass `enabled` from useIsAdmin so a non-admin never makes the call.
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'

export interface AdminDevice {
  id: string
  name: string
  bluetooth_id: string
  device_eui: string | null
  organisation: { id: string; name: string } | null
  latest_deployment: {
    id: string
    name: string
    deployment_start: string
    deployment_end: string | null
    project: { id: string; name: string }
  } | null
}

export function useAdminDevices(enabled: boolean) {
  return useQuery({
    queryKey: ['admin', 'devices'],
    queryFn: async (): Promise<AdminDevice[]> => {
      const r = (await apiClient.get('/api/admin/devices')) as { data?: AdminDevice[] }
      return r.data ?? []
    },
    enabled,
  })
}
