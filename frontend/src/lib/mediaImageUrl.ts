// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Which URL a grid card or the photo viewer puts in an <img> (#300).
import { getLocalPreview } from './localPreviewStore'

interface Renditions {
  thumbnail_url: string | null
  preview_url: string | null
}

/**
 * The image to show for a photo, or null when there is nothing a plain <img> can load.
 *
 * The public rendition comes first (MEDIA_PREP): the thumbnail for a card, the larger preview
 * for the viewer. Then the user's own copy of a just-uploaded file, then a public http(s)
 * original. A `gdrive://` original is never returned: the only way to it is
 * `/api/media/{id}/image`, which needs a Bearer header an <img> never sends (#124, #175), so
 * the caller shows "Processing…" or "No preview yet" instead of a broken image.
 */
export function mediaImageUrl(
  media: { file_path: string | null; media_assets: Renditions | Renditions[] | null | undefined },
  size: 'thumb' | 'full',
  localPreview?: string | null,
): string | null {
  const asset = Array.isArray(media.media_assets) ? media.media_assets[0] : media.media_assets
  const rendition = size === 'full'
    ? (asset?.preview_url || asset?.thumbnail_url)
    : (asset?.thumbnail_url || asset?.preview_url)
  if (rendition) return rendition
  if (localPreview) return localPreview
  const path = media.file_path
  return path && (path.startsWith('http://') || path.startsWith('https://')) ? path : null
}

/** `mediaImageUrl` with the user's own copy of a just-uploaded file, as the grid and the viewer show it. */
export function displayImageUrl(
  media: { file_name: string | null; file_path: string | null; media_assets: Renditions | Renditions[] | null | undefined },
  size: 'thumb' | 'full',
): string | null {
  return mediaImageUrl(media, size, getLocalPreview(media.file_name))
}
