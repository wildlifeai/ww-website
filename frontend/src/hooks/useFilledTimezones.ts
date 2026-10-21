// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useFilledTimezones: the deployments as given, then with the zones the backend fills (#309).
// The rules live in lib/deploymentTimezones.ts. The page renders straight away; a failure
// leaves the rows as they are, so capture times stay in UTC as before.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { fillTimezones, missingTimezoneIds, withTimezones, type TimezoneRow } from '../lib/deploymentTimezones'

export function useFilledTimezones<T extends TimezoneRow>(rows: T[]): T[] {
  const ids = useMemo(() => missingTimezoneIds(rows), [rows])
  const { data } = useQuery({
    queryKey: ['deployment-timezones', ids],
    queryFn: () => fillTimezones(apiClient.post, ids),
    enabled: ids.length > 0,
    staleTime: Infinity,
    retry: false,
  })
  return useMemo(() => (data ? withTimezones(rows, data) : rows), [rows, data])
}
