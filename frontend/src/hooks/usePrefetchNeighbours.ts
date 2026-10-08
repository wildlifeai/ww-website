// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// usePrefetchNeighbours: when the viewer opens or moves to a photo, load the previews of the
// photos either side in the background, so the arrow keys show them without a wait (#286).
import { useEffect } from 'react'
import { neighbourPreviewUrls } from '../lib/viewerPrefetch'

export function usePrefetchNeighbours(
  list: Parameters<typeof neighbourPreviewUrls>[0] | undefined,
  index: number,
): void {
  const key = neighbourPreviewUrls(list ?? [], index).join('\n')

  useEffect(() => {
    // Held until the next photo, so the loads are not collected before they land. Not
    // cancelled: a quick run of arrow presses still wants the photos it passes.
    const images = key.split('\n').filter(Boolean).map(url => {
      const img = new Image()
      img.src = url
      return img
    })
    return () => { images.length = 0 }
  }, [key])
}
