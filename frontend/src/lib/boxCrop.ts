// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// On-screen crop of a labelled box, for a label that has a box but no crop file (a box a
// person drew, an AI person detection). Boxes are fractions of the photo from its top-left.

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

/** Context kept around the box, as a fraction of its size on each side. */
export const BOX_PAD = 0.1

/** The box widened by BOX_PAD on each side, kept inside the photo. */
export function paddedBox(b: Box, pad = BOX_PAD): Box {
  const x = Math.max(0, b.x - b.w * pad)
  const y = Math.max(0, b.y - b.h * pad)
  return { x, y, w: Math.min(1, b.x + b.w * (1 + pad)) - x, h: Math.min(1, b.y + b.h * (1 + pad)) - y }
}

/**
 * Where to put the whole photo in a card so the padded box fits it without stretching.
 * `widthPct` is the photo's width as a percentage of the card's; `cx`, `cy` are the box centre
 * as fractions of the photo, to be moved to the card's centre. Aspects are width / height.
 */
export function boxCropLayout(box: Box, photoAspect: number, cardAspect: number) {
  const b = paddedBox(box)
  const boxAspect = (b.w * photoAspect) / b.h
  const widthPct = boxAspect >= cardAspect ? 100 / b.w : (100 / b.h) * (photoAspect / cardAspect)
  return { widthPct, cx: b.x + b.w / 2, cy: b.y + b.h / 2 }
}

/** The box of an observation, when it has a usable one. */
export function boxOf(o: { bbox_x?: number | null; bbox_y?: number | null; bbox_w?: number | null; bbox_h?: number | null }): Box | null {
  const { bbox_x: x, bbox_y: y, bbox_w: w, bbox_h: h } = o
  if (x == null || y == null || w == null || h == null || w <= 0 || h <= 0) return null
  return { x, y, w, h }
}
