// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Group by on the review grid. Groups split the photos on the page, so a grouping that would
// scatter 100 photos across 100 headers is not offered (UI audit 02 #9), nor one that leaves them
// all in a single group, and the species, sex and life stage of a photo come from its verdict,
// the row its card shows (#10, lib/observations).
import { photoVerdict, type PresenceFields } from './observations'

export type GroupBy =
  | 'none' | 'cluster' | 'species' | 'sex' | 'life_stage' | 'annotation_type' | 'project' | 'deployment' | 'model' | 'annotator'

export const GROUP_LABELS: Record<GroupBy, string> = {
  none: 'None', cluster: 'Cluster', species: 'Species', sex: 'Sex', life_stage: 'Life stage',
  annotation_type: 'Annotation type', project: 'Project', deployment: 'Deployment', model: 'AI model', annotator: 'Annotator',
}

/** Above this many deployments in view, a 100-photo page splits into groups of one or two. */
export const DEPLOYMENT_GROUP_LIMIT = 12

/**
 * Whether a grouping makes sense for the deployments in view: clusters belong to one
 * deployment's embedding run, and deployment groups only when there are few of them.
 */
export function groupingAvailable(groupBy: GroupBy, deploymentCount: number): boolean {
  if (groupBy === 'cluster') return deploymentCount === 1
  if (groupBy === 'deployment') return deploymentCount > 1 && deploymentCount <= DEPLOYMENT_GROUP_LIMIT
  return true
}

/** The names the project and deployment groupings show, keyed by deployment id. */
export interface GroupNames {
  deployments: ReadonlyMap<string, string>
  projects: ReadonlyMap<string, string>
}

export interface GroupablePhoto {
  deployment_id: string
  observations: readonly GroupableObservation[]
}

/**
 * The groupings to offer, in menu order: those the deployments in view allow and that split the
 * page's photos into at least two groups, so sex is not offered when no photo has one. Clusters
 * are offered on the deployment alone, since their groups load only once chosen. While the page
 * has no photos yet, nothing is ruled out, so a chosen grouping survives the page change.
 */
export function availableGroupings(deploymentCount: number, photos: readonly GroupablePhoto[], names: GroupNames): GroupBy[] {
  return (Object.keys(GROUP_LABELS) as GroupBy[]).filter(g => g !== 'none' && groupingAvailable(g, deploymentCount)
    && (g === 'cluster' || photos.length === 0 || splitsIntoGroups(photos, p => photoGroupKey(g, p, names))))
}

/** Whether the items fall into at least two groups. */
export function splitsIntoGroups<T>(items: readonly T[], keyOf: (item: T) => string): boolean {
  let first: string | undefined
  for (const item of items) {
    const key = keyOf(item)
    if (first === undefined) first = key
    else if (key !== first) return true
  }
  return false
}

/** The group a photo falls in, for every grouping but clusters. */
export function photoGroupKey(groupBy: GroupBy, photo: GroupablePhoto, names: GroupNames): string {
  if (groupBy === 'deployment') return names.deployments.get(photo.deployment_id) || photo.deployment_id.slice(0, 8)
  if (groupBy === 'project') return names.projects.get(photo.deployment_id) || '(Unknown project)'
  return observationGroupKey(groupBy, photo.observations)
}

export interface GroupableObservation extends PresenceFields {
  sex?: string | null
  life_stage?: string | null
  bbox_x?: number | null
  classification_method?: string | null
  classified_by?: string | null
  source_model_version?: string | null
}

/** The group a photo falls in, for the groupings that read its observations. */
export function observationGroupKey(groupBy: GroupBy, obs: readonly GroupableObservation[]): string {
  switch (groupBy) {
    case 'species': {
      const { labelObs, isEmpty } = photoVerdict(obs)
      return labelObs?.scientific_name || (isEmpty ? '(Empty)' : '(No species)')
    }
    case 'sex':
      return photoVerdict(obs).labelObs?.sex || '(Unknown)'
    case 'life_stage':
      return photoVerdict(obs).labelObs?.life_stage || '(Unknown)'
    case 'annotation_type':
      return obs.some(o => o.bbox_x != null) ? 'Bounding box' : 'Whole image'
    case 'model': {
      const ai = obs.find(o => o.classification_method === 'machine')
      return ai?.source_model_version || ai?.classified_by || '(No AI model)'
    }
    case 'annotator':
      return obs.find(o => o.classification_method === 'human')?.classified_by || '(No annotator)'
    default:
      return '(Ungrouped)'
  }
}

/** Groups in first-seen order, then largest first. */
export function groupInOrder<T>(items: readonly T[], keyOf: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const key = keyOf(item)
    const list = groups.get(key)
    if (list) list.push(item)
    else groups.set(key, [item])
  }
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length)
}
