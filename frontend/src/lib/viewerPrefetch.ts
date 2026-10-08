// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Which previews the photo viewer loads ahead, so the arrow keys show the next photo at once
// (#286).

interface Renditions {
  thumbnail_url: string | null
  preview_url: string | null
}

/** Photos on each side of the open one whose previews are loaded ahead. */
export const PREFETCH_SPAN = 2

/**
 * The preview (else the thumbnail) of the photos up to `span` either side of `index`, nearest
 * first and the next one before the previous one. Photos with neither are skipped.
 */
export function neighbourPreviewUrls(
  list: ReadonlyArray<{ media_assets: Renditions | Renditions[] | null }>,
  index: number,
  span = PREFETCH_SPAN,
): string[] {
  if (index < 0) return []
  const urls: string[] = []
  for (let step = 1; step <= span; step++) {
    for (const i of [index + step, index - step]) {
      const a = list[i]?.media_assets
      const asset = Array.isArray(a) ? a[0] : a
      const url = asset?.preview_url || asset?.thumbnail_url
      if (url) urls.push(url)
    }
  }
  return urls
}
