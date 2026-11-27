// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A Wildlife Brain group that mixes species (#450, label checks decision 6): which labels a confirm
// of the whole group would change, and the words for its mark and its warning. Whether a group
// mixes, broader and narrower names apart, is worked out by the API (domain/cluster_labels.py).
import type { ClusterLabels } from '../hooks/useBrain'

export interface LabelChange {
  name: string
  /** Photos whose label the confirm changes. */
  photos: number
}

const nameOf = (l: { vernacular_name?: string | null; scientific_name: string }) => l.vernacular_name || l.scientific_name
const nameKey = (name: string) => name.trim().split(/\s+/).join(' ').toLowerCase()

/**
 * The labels a confirm as `pick` changes: the members nobody has labelled whose species is another
 * name. A member a person labelled keeps its label (the confirm skips it).
 */
export function labelChanges(cluster: ClusterLabels | undefined, pick: { scientific_name: string }): LabelChange[] {
  const picked = nameKey(pick.scientific_name)
  return (cluster?.labels ?? []).flatMap(l =>
    l.unreviewed > 0 && nameKey(l.scientific_name) !== picked ? [{ name: nameOf(l), photos: l.unreviewed }] : [])
}

/** The changes to warn about before a confirm as `pick`, or null to confirm at once: only a mixed group warns. */
export function confirmWarning(cluster: ClusterLabels | undefined, pick: { scientific_name: string }): LabelChange[] | null {
  if (!cluster?.mixed) return null
  const changes = labelChanges(cluster, pick)
  return changes.length > 0 ? changes : null
}

/** "Mixed group: 12 Ship rat and 3 House mouse photos would become Norway rat." */
export function warningText(changes: readonly LabelChange[], pickName: string): string {
  const parts = changes.map(c => `${c.photos} ${c.name}`)
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0]
  const total = changes.reduce((n, c) => n + c.photos, 0)
  return `Mixed group: ${list} ${total === 1 ? 'photo' : 'photos'} would become ${pickName}.`
}

/** The mark's tooltip: each species with its photos, most first. */
export function mixedTitle(cluster: ClusterLabels): string {
  return cluster.labels.map(l => `${nameOf(l)}: ${l.photos}`).join(', ')
}
