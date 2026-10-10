// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Save a model's label map through PUT /api/models/{model_id}/label-map (#324). The route runs
// LM-10 (backend/app/domain/label_map.py) before the write and writes as the caller, so RLS
// decides who may edit. A map that breaks LM-10 comes back as a 422 with one problem per label.
import { ApiError, apiClient } from './apiClient'

/** LM-10 problems keyed by the label they belong to. */
export type LabelProblems = Record<string, string>

/**
 * The per-label problems in a refused save, or null when the error is something else. A
 * refusal from the database's own LM-10 CHECK names no label, so it is null too and its
 * message is shown instead.
 */
export function labelMapProblems(err: unknown): LabelProblems | null {
  if (!(err instanceof ApiError)) return null
  const problems = (err.detail as { problems?: unknown } | undefined)?.problems
  if (!problems || typeof problems !== 'object' || Array.isArray(problems)) return null
  const byLabel = Object.entries(problems).filter(([, v]) => typeof v === 'string')
  return byLabel.length ? (Object.fromEntries(byLabel) as LabelProblems) : null
}

/** Save the whole map. Null when saved, the problems when LM-10 refused it; other errors throw. */
export async function saveLabelMap(modelId: string, labelMap: Record<string, unknown>): Promise<LabelProblems | null> {
  try {
    await apiClient.put(`/api/models/${modelId}/label-map`, { label_map: labelMap })
    return null
  } catch (err) {
    const problems = labelMapProblems(err)
    if (problems) return problems
    throw err
  }
}
