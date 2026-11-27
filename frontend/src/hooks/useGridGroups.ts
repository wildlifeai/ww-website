// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useGridGroups: the review grid's Group by. Groups split the page (lib/gridGrouping); cluster
// groups come from the multi-cluster API, fetched only while grouped by cluster, and the groups
// the reviewer collapsed are kept for the current grouping. An unconfirmed cluster's group carries
// the species its members carry, for the mixed mark and Label all's warning (#450). It also reports
// whether the one deployment in view has a Wildlife Brain run, for the Review tab.
import { useMemo, useState } from 'react'
import type { MediaRecord } from '../lib/gridMedia'
import { availableGroupings, groupInOrder, photoGroupKey, type GroupBy, type GroupNames } from '../lib/gridGrouping'
import { useClusterLabels, useClusters, useMultiClusters, type ClusterLabels } from './useBrain'

/** A cluster's group: its confirm-able row, and the species of its members while it is unconfirmed. */
export interface ClusterGroup {
  id: string
  confirmed: boolean
  labels?: ClusterLabels
  labelsLoading: boolean
}

const NONE: ReadonlySet<string> = new Set()

interface Args {
  /** The page's cards, uploading placeholders included. */
  cards: MediaRecord[]
  /** The reviewer's choice; a grouping the selection does not allow falls back to none. */
  choice: GroupBy
  deploymentIds: string[]
  deploymentNames: ReadonlyMap<string, string>
  /** Each deployment's project name, keyed by deployment id. */
  projectNames: ReadonlyMap<string, string>
  brainOn: boolean
  clusterThreshold: number
}

export function useGridGroups({ cards, choice, deploymentIds, deploymentNames, projectNames, brainOn, clusterThreshold }: Args) {
  const names = useMemo<GroupNames>(() => ({ deployments: deploymentNames, projects: projectNames }), [deploymentNames, projectNames])
  const options = useMemo(
    () => availableGroupings(deploymentIds.length, cards, names).filter(g => g !== 'cluster' || brainOn),
    [deploymentIds.length, cards, names, brainOn],
  )
  const groupBy: GroupBy = options.includes(choice) ? choice : 'none'
  const singleDeployment = deploymentIds.length === 1 ? deploymentIds[0] : null

  const clustersQ = useMultiClusters(groupBy === 'cluster' ? deploymentIds : [], clusterThreshold)
  // The Review tab offers Review clusters and UMAP once the deployment has an embedding run,
  // and Run Wildlife Brain before (one row per cluster, so the read is small).
  const clusterRun = useClusters(brainOn && singleDeployment ? singleDeployment : undefined)

  // Map a cluster group's display label → its confirm-able row id, so the group
  // header can offer "label all" (only for unconfirmed clusters). Mirrors the
  // label logic in groups below.
  const clusterRows = useMemo(() => {
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
      return photoGroupKey(groupBy, m, names)
    })
  }, [cards, groupBy, names, clustersQ.data])

  const openClusterIds = useMemo(
    () => (groups ?? []).flatMap(([label]) => {
      const c = clusterRows.get(label)
      return c && !c.confirmed ? [c.id] : []
    }),
    [groups, clusterRows],
  )
  const labelsQ = useClusterLabels(groupBy === 'cluster' ? singleDeployment ?? undefined : undefined, openClusterIds)
  const clusterByLabel = useMemo(() => {
    const m = new Map<string, ClusterGroup>()
    for (const [label, c] of clusterRows) m.set(label, { ...c, labels: labelsQ.data?.[c.id], labelsLoading: labelsQ.isLoading })
    return m
  }, [clusterRows, labelsQ.data, labelsQ.isLoading])

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
    embeddingRunLoading: clusterRun.isLoading,
  }
}
