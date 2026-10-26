// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Whether a feature-flagged backend feature is switched on for this site (UI audit decision 6).
// A route whose flag is off answers HTTP 200 with the error code FEATURE_DISABLED, which
// apiClient throws as an ApiError. There is no endpoint that lists the flags, so the hook asks
// one cheap route per feature and reads the answer.

export type Feature = 'intelligence' | 'brain' | 'activeLearning' | 'gbfReport'

/**
 * The route each feature is probed with. The flags are global to the backend; the id only has
 * to be one the user can read, of the kind each route takes.
 */
export const FEATURE_PROBES: Record<Feature, (scopeId: string) => string> = {
  /** FF_INTELLIGENCE_ENABLED: Dataset Health. Takes a project id. */
  intelligence: id => `/api/intelligence/alerts/${id}`,
  /** FF_WILDLIFE_BRAIN_ENABLED: Cluster Review, UMAP. Takes a deployment id. */
  brain: id => `/api/brain/embedding-runs/${id}`,
  /** FF_ACTIVE_LEARNING_ENABLED: the active-learning score and the QA agreement. Takes a deployment id. */
  activeLearning: id => `/api/brain/review-queue/${id}?limit=1`,
  /** FF_GBF_REPORT_ENABLED: the GBF report card. Takes a project id; one day in 2000 reads next to nothing. */
  gbfReport: id => `/api/reports/gbf?project_ids=${id}&from=2000-01-01&to=2000-01-01`,
}

/** True for the error a route answers while its feature flag is off. */
export function isFeatureDisabled(err: unknown): boolean {
  return (err as { code?: unknown } | null)?.code === 'FEATURE_DISABLED'
}

/**
 * Runs a probe: true when the route answered, false when it said FEATURE_DISABLED. Any other
 * failure (no access to that id, network) says nothing about the flag, so it is rethrown.
 */
export async function probeFeature(call: () => Promise<unknown>): Promise<boolean> {
  try {
    await call()
    return true
  } catch (err) {
    if (isFeatureDisabled(err)) return false
    throw err
  }
}
