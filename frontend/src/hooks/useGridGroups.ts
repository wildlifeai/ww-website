// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridGroups: the review grid's Group by. Groups split the page (lib/gridGrouping); cluster
// groups come from the multi-cluster API, fetched only while grouped by cluster, and the groups
// the reviewer collapsed are kept for the current grouping.
import { useMemo, useState } from 'react'
import type { MediaRecord } from '../lib/gridMedia'
import { availableGroupings, groupInOrder, observationGroupKey, type GroupBy } from '../lib/gridGrouping'
import { useClusters, useMultiClusters } from './useBrain'

const NONE: ReadonlySet<string> = new Set()

interface Args {
  /** The page's cards, uploading placeholders included. */
  cards: MediaRecord[]
  /** The reviewer's choice; a grouping the selection does not allow falls back to none. */
  choice: GroupBy
  deploymentIds: string[]
  deploymentNames: ReadonlyMap<string, string>
  brainOn: boolean
  clusterThreshold: number
}

export function useGridGroups({ cards, choice, deploymentIds, deploymentNames, brainOn, clusterThreshold }: Args) {
  const options = availableGroupings(deploymentIds.length).filter(g => g !== 'cluster' || brainOn)
  const groupBy: GroupBy = options.includes(choice) ? choice : 'none'
  const singleDeployment = deploymentIds.length === 1 ? deploymentIds[0] : null

  const clustersQ = useMultiClusters(groupBy === 'cluster' ? deploymentIds : [], clusterThreshold)
  // Review clusters and UMAP need an embedding run on the deployment.
  const clusterRun = useClusters(groupBy === 'cluster' && singleDeployment ? singleDeployment : undefined)

  // Map a cluster group's display label → its confirm-able row id, so the group
  // header can offer "label all" (only for unconfirmed clusters). Mirrors the
  // label logic in groups below.
  const clusterByLabel = useMemo(() => {
    const m = new Map<string, { id: string; confirmed: boolean }>()
    if (groupBy !== 'cluster') return m
    for (const c of clustersQ.data?.clusters ?? []) {
      const label = c.scientific_name ?? `Cluster ${c.cluster_id}`
      if (!m.has(label)) m.set(label, { id: c.id, confirmed: !!c.scientific_name })
    }
    return m
  }, [groupBy, clustersQ.data])

  const groups = useMemo(() => {
    if (groupBy === 'none') return null
    // Cluster lookups (from the multi-cluster API): media → cluster_id, the set
    // of outliers, and any confirmed species name per cluster for nicer labels.
    const clusterData = clustersQ.data
    const mediaCluster = clusterData?.media_clusters ?? {}
    const outlierSet = new Set(clusterData?.outlier_media_ids ?? [])
    const clusterNames = new Map<number, string>()
    for (const c of clusterData?.clusters ?? []) {
      if (c.scientific_name && !clusterNames.has(c.cluster_id)) clusterNames.set(c.cluster_id, c.scientific_name)
    }

    return groupInOrder(cards, m => {
      if (groupBy === 'cluster') {
        if (!clusterData) return '(Loading clusters…)'
        if (outlierSet.has(m.id)) return 'Outliers'
        const cid = mediaCluster[m.id]
        // Not in any cluster yet (e.g. blank frame with no animal crop, or
        // embeddings not run for this deployment).
        if (cid == null) return 'Not yet clustered'
        return clusterNames.get(cid) ?? `Cluster ${cid}`
      }
      if (groupBy === 'deployment') return deploymentNames.get(m.deployment_id) || m.deployment_id.slice(0, 8)
      return observationGroupKey(groupBy, m.observations)
    })
  }, [cards, groupBy, deploymentNames, clustersQ.data])

  // Groups the reviewer collapsed, for the current grouping.
  const [collapsedAt, setCollapsedAt] = useState<{ groupBy: GroupBy; labels: ReadonlySet<string> }>({ groupBy: 'none', labels: NONE })
  const collapsed = collapsedAt.groupBy === groupBy ? collapsedAt.labels : NONE
  const setCollapsed = (labels: ReadonlySet<string>) => setCollapsedAt({ groupBy, labels })
  const toggleCollapsed = (label: string) => {
    const next = new Set(collapsed)
    if (!next.delete(label)) next.add(label)
    setCollapsed(next)
  }

  return {
    options, groupBy, groups, clusterByLabel, collapsed, setCollapsed, toggleCollapsed,
    clustersLoading: clustersQ.isLoading,
    embeddingRunId: clusterRun.data?.embedding_run_id ?? null,
  }
}
