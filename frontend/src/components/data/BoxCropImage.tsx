// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The Labels view's crop for a label with a box but no crop file: the whole preview, scaled and
// centred so the padded box fits the 4:3 card (IMAGE_AREA) without stretching.
import { useState } from 'react'
import { boxCropLayout, type Box } from '../../lib/boxCrop'

const CARD_ASPECT = 4 / 3

export function BoxCropImage({ src, box, alt }: { src: string; box: Box; alt: string }) {
  // The photo's shape is known once it loads; WW500 photos are 4:3 until then.
  const [photoAspect, setPhotoAspect] = useState(4 / 3)
  const { widthPct, cx, cy } = boxCropLayout(box, photoAspect, CARD_ASPECT)
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onLoad={(e) => {
        const img = e.currentTarget
        if (img.naturalWidth && img.naturalHeight) setPhotoAspect(img.naturalWidth / img.naturalHeight)
      }}
      onError={(e) => { e.currentTarget.style.display = 'none' }}
      style={{
        position: 'absolute', left: '50%', top: '50%', width: `${widthPct}%`, maxWidth: 'none', height: 'auto',
        transform: `translate(${-cx * 100}%, ${-cy * 100}%)`,
      }}
    />
  )
}
