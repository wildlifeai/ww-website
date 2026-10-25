// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Placement rules for the deployment map (UI audit 04 #12): the box that frames every marker,
// and a small fan-out for deployments that share a site, so a re-deployment at the same spot is
// not hidden under the first one.

export interface MapPoint {
  id: string
  latitude: number
  longitude: number
}

export interface Bounds {
  south: number
  west: number
  north: number
  east: number
}

/** The box around every point, or null when there are none. */
export function boundsOf(points: MapPoint[]): Bounds | null {
  if (points.length === 0) return null
  let south = Infinity, west = Infinity, north = -Infinity, east = -Infinity
  for (const p of points) {
    south = Math.min(south, p.latitude)
    north = Math.max(north, p.latitude)
    west = Math.min(west, p.longitude)
    east = Math.max(east, p.longitude)
  }
  return { south, west, north, east }
}

/** Points closer than this (about a metre) count as one site. */
const SITE_DECIMALS = 5

/**
 * Screen offsets in pixels, [dx, dy], for points that share a site: each group is spread on a
 * circle around the site, wide enough that neighbours sit `spacing` pixels apart. A point alone
 * at its site is not in the map.
 */
export function spreadOffsets(points: MapPoint[], spacing = 26, minRadius = 16): Map<string, [number, number]> {
  const sites = new Map<string, string[]>()
  for (const p of points) {
    const key = `${p.latitude.toFixed(SITE_DECIMALS)},${p.longitude.toFixed(SITE_DECIMALS)}`
    const ids = sites.get(key)
    if (ids) ids.push(p.id)
    else sites.set(key, [p.id])
  }
  const offsets = new Map<string, [number, number]>()
  for (const ids of sites.values()) {
    if (ids.length < 2) continue
    const radius = Math.max(minRadius, (ids.length * spacing) / (2 * Math.PI))
    ids.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / ids.length - Math.PI / 2
      offsets.set(id, [Math.round(radius * Math.cos(angle)), Math.round(radius * Math.sin(angle))])
    })
  }
  return offsets
}
