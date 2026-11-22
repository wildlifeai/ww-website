// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The review grid's "Run AI again" on the selected photos (POST /api/media/run-selected, #452).
// The route answers at once with an `ai_run` job: the ARQ worker runs it on Azure, the next REANNZ
// GPU batch on AWS. The grid follows it like any AI job, in the processing banner (GET /api/jobs)
// and in Processing history, and reloads when it ends (useBusyDeployments).

type Post = (path: string, body: unknown) => Promise<unknown>

export interface AiModel {
  id: string
  label: string
  description: string
  /** The pipeline steps the model runs, in pipeline order. */
  steps: readonly string[]
}

/** What the picker offers. The Wildlife Brain's embedding is not here: it follows every AI run where it is on. */
export const AI_MODELS: readonly AiModel[] = [
  { id: 'speciesnet', label: 'SpeciesNet', description: 'Detector + species classifier', steps: ['speciesnet', 'animal_crop'] },
  { id: 'bioclip', label: 'BioCLIP', description: 'Zero-shot secondary classifier on the animal crops', steps: ['bioclip'] },
]

export const AI_NOT_QUEUED = 'The AI run was not queued. Try again later.'
export const AI_BUSY =
  'AI is already queued or running on a deployment of these photos. Try again when that job finishes: '
  + 'it shows in the banner above the photos and in Processing history.'
const AI_OFF = 'The AI pipeline is turned off on this server.'

/** The pipeline steps for the chosen models, in pipeline order. */
export function aiRunSteps(models: readonly string[]): string[] {
  const chosen = new Set(models)
  return AI_MODELS.filter(m => chosen.has(m.id)).flatMap(m => m.steps)
}

/** The toast once the run is queued. */
export function aiRunQueued(photos: number): string {
  return `AI queued for ${photos} photo${photos === 1 ? '' : 's'}. The banner above the photos shows its progress.`
}

/** Why the run did not start, in words the grid can show. */
export function aiRunRefusal(err: unknown): string {
  const code = (err as { code?: string } | null)?.code
  if (code === 'PIPELINE_BUSY') return AI_BUSY
  if (code === 'FEATURE_DISABLED') return AI_OFF
  return err instanceof Error && err.message ? err.message : AI_NOT_QUEUED
}

/** Queue the chosen models on the photos. Returns how many photos the job covers; throws an Error with the message to show. */
export async function startAiRun(mediaIds: readonly string[], models: readonly string[], post: Post): Promise<number> {
  let res: { data?: { job_id?: string; media_count?: number } }
  try {
    res = await post('/api/media/run-selected', { media_ids: mediaIds, steps: aiRunSteps(models) }) as typeof res
  } catch (err) {
    throw new Error(aiRunRefusal(err))
  }
  if (!res?.data?.job_id) throw new Error(AI_NOT_QUEUED)
  return res.data.media_count ?? mediaIds.length
}
