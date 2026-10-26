// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Publishing photos to iNaturalist from Cluster Review, through useINat().publish and
// POST /api/inat/publish (backend/app/domain/inaturalist_publish.py): what blocks it, and the
// notice its answer or its error gives. Success is shown only when something reached iNaturalist.

import { isInatOff } from './inatOff'

/** The route's limit on photos per request (`INatPublishRequest.media_ids`, max_length 200). */
export const INAT_PUBLISH_MAX = 200

/** Where an account is connected: Toolkit has the iNaturalist sign-in and the pasted-token path. */
export const INAT_CONNECT_PATH = '/toolkit'

/** Where a deployment's location is set (Insights > Deployments, Edit location). */
export const DEPLOYMENT_LOCATION_PATH = '/insights?tab=deployments'

export interface NoticeLink {
  to: string
  label: string
  /** An iNaturalist page, opened in a new tab; otherwise a route of this site. */
  external?: boolean
}

/** A card's notice: what the last action did, kept until the next one. */
export interface CardNotice {
  ok: boolean
  text: string
  link?: NoticeLink
}

/** The answer of POST /api/inat/publish, as far as the notice reads it. */
export interface InatPublishAnswer {
  observations_created: number
  photos_uploaded: number
  skipped_bycatch: number
  skipped_already_published: number
  /** Encounters (or deployments without a location) that did not become an observation. */
  errors: number
  observations?: { uri: string | null; photo_errors?: number }[]
  /** Photos the rounds did not reach (lib/inatPublishRounds.ts). */
  remaining_media_ids?: string[]
}

const CONNECT_LINK: NoticeLink = { to: INAT_CONNECT_PATH, label: 'Connect it in Toolkit' }

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** Why the selection cannot be published, or null when it can. `connected` null is still unknown. */
export function inatBlocker(s: { connected: boolean | null; hasLocation?: boolean; selected: number }): CardNotice | null {
  if (s.connected === false) {
    return { ok: false, text: 'Your iNaturalist account is not connected.', link: CONNECT_LINK }
  }
  if (s.hasLocation === false) {
    return {
      ok: false,
      text: 'This deployment has no location, which iNaturalist needs.',
      link: { to: DEPLOYMENT_LOCATION_PATH, label: 'Set it in Insights' },
    }
  }
  if (s.selected > INAT_PUBLISH_MAX) {
    return { ok: false, text: `Select ${INAT_PUBLISH_MAX} photos or fewer: iNaturalist takes ${INAT_PUBLISH_MAX} at a time.` }
  }
  return null
}

/** The notice for the route's answer: ok only when photos went up and nothing failed or was left. */
export function inatPublishNotice(r: InatPublishAnswer): CardNotice {
  const photoErrors = (r.observations ?? []).reduce((n, o) => n + (o.photo_errors ?? 0), 0)
  const left = r.remaining_media_ids?.length ?? 0
  // A photo whose encounter is already on iNaturalist joins that observation instead of a new one.
  const published = r.observations_created > 0 || r.photos_uploaded > 0
  const skipped = r.skipped_already_published + r.skipped_bycatch
  const lines = [
    r.observations_created > 0
      ? `Published ${count(r.observations_created, 'observation')} with ${count(r.photos_uploaded, 'photo')} to iNaturalist.`
      : published
        ? `Added ${count(r.photos_uploaded, 'photo')} to observations already on iNaturalist.`
        : 'Nothing published.',
  ]
  if (skipped > 0) {
    const why = [
      r.skipped_already_published > 0 && `${r.skipped_already_published} already on iNaturalist`,
      r.skipped_bycatch > 0 && `${r.skipped_bycatch} not an animal or showing a person`,
    ].filter(Boolean)
    lines.push(`Skipped ${skipped}: ${why.join(', ')}.`)
  }
  if (r.errors > 0) lines.push(`${count(r.errors, 'encounter')} failed.`)
  if (photoErrors > 0) lines.push(`${count(photoErrors, 'photo')} did not upload.`)
  if (left > 0) lines.push(`${count(left, 'photo')} not reached: publish again to carry on.`)
  if (!published && skipped === 0 && r.errors === 0 && left === 0) lines.push('None of these photos were found.')

  const uri = r.observations?.find(o => o.uri)?.uri
  return {
    ok: published && r.errors === 0 && photoErrors === 0 && left === 0,
    text: lines.join(' '),
    link: published && uri ? { to: uri, label: 'View on iNaturalist', external: true } : undefined,
  }
}

const messageOf = (err: unknown) => String((err as { message?: unknown } | null)?.message ?? err)

/** The notice for a refused or failed publish. */
export function inatErrorNotice(err: unknown): CardNotice {
  const msg = messageOf(err)
  // The route's 400 when the user has no stored token, or a pasted one has expired (about 24 h).
  if (/not connected to inaturalist/i.test(msg)) {
    return { ok: false, text: 'Not published: your iNaturalist account is not connected.', link: CONNECT_LINK }
  }
  if (isInatOff(err)) return { ok: false, text: 'Not published: iNaturalist is switched off on this server.' }
  return { ok: false, text: `Not published: ${msg}` }
}
