// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// POST /api/inat/publish answers inside the request timeout: it starts no photo after about two
// minutes and hands back the ones it did not reach as `remaining_media_ids`
// (backend/app/domain/inaturalist_publish.py). useINat().publish sends those again, round after
// round, and answers once with the rounds added up. Kept apart from lib/inatPublish.ts: useINat
// runs on every signed-in page, so what it imports lands in the first load.

/** The answer of POST /api/inat/publish. */
export interface INatPublishResult {
  observations_created: number
  photos_uploaded: number
  skipped_bycatch: number
  skipped_already_published: number
  errors: number
  observations: {
    inat_observation_id: number | null
    uri: string | null
    species_guess: string | null
    media_count: number
    photo_errors?: number
  }[]
  /** The photos the request did not reach in its time; empty when it got through. */
  remaining_media_ids?: string[]
}

const didWork = (r: INatPublishResult) =>
  r.observations_created + r.photos_uploaded + r.errors > 0 || r.observations.some(o => (o.photo_errors ?? 0) > 0)

/**
 * Sends the selection, then what each answer hands back, until nothing is left, and adds the
 * rounds up. The skipped counts are the first round's: a later round counts the photos earlier
 * ones published as already on iNaturalist. Each round tries at least one photo, so it stops
 * after one round per photo, and earlier when a round did nothing.
 */
export async function publishInRounds(
  post: (mediaIds: string[]) => Promise<INatPublishResult>,
  mediaIds: string[],
): Promise<INatPublishResult> {
  let last = await post(mediaIds)
  let total = last
  for (let round = 1; round < mediaIds.length && (last.remaining_media_ids?.length ?? 0) > 0 && didWork(last); round++) {
    last = await post(last.remaining_media_ids ?? [])
    total = {
      ...total,
      observations_created: total.observations_created + last.observations_created,
      photos_uploaded: total.photos_uploaded + last.photos_uploaded,
      errors: total.errors + last.errors,
      observations: [...total.observations, ...last.observations],
    }
  }
  return { ...total, remaining_media_ids: last.remaining_media_ids ?? [] }
}
