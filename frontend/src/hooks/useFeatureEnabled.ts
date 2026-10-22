// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { FEATURE_PROBES, probeFeature, type Feature } from '../lib/featureFlags'

/**
 * Whether a feature-flagged feature answers on this site: true once its probe route answered,
 * false while unknown, on FEATURE_DISABLED, or when the probe failed for another reason. Hide an
 * entry point unless it is true. `scopeId` is a project id for 'intelligence' and a deployment
 * id for 'brain' and 'activeLearning' (see FEATURE_PROBES).
 *
 * The flags are global, so the answer is cached once per feature, not per id: a page that shows
 * a link on every row makes one request, not one per row.
 */
export function useFeatureEnabled(feature: Feature, scopeId?: string | null): boolean {
  const { data } = useQuery({
    queryKey: ['feature', feature],
    queryFn: () => probeFeature(() => apiClient.get(FEATURE_PROBES[feature](scopeId as string))),
    enabled: !!scopeId,
    // A flag changes only when the backend is redeployed.
    staleTime: 30 * 60_000,
  })
  return data === true
}
