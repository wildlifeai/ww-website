import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { reviewInBatches, type ReviewBatchResult } from '../lib/clusterReview'

// ── Types (mirror backend brain router responses) ──────────────────────────

export interface ClusterAssignment {
  id: string
  cluster_id: number
  taxon_id: string | null
  scientific_name: string | null
  is_outlier_cluster: boolean
  image_count: number
  mean_confidence: number | null
  purity_score: number | null
  review_depth: 'bulk' | 'sample' | 'full' | null
  review_state: 'open' | 'locked' | 'confirmed' | 'conflicted'
}

export interface UmapPoint {
  media_id: string
  umap_x: number
  umap_y: number
  cluster_id: number
  is_outlier: boolean
  cluster_purity: 'high' | 'medium' | 'low' | null
}

export interface SimilarHit {
  media_id: string
  score: number
  payload: Record<string, any>
}

// ── Queries ────────────────────────────────────────────────────────────────

export function useClusters(deploymentId?: string) {
  return useQuery({
    queryKey: ['brain', 'clusters', deploymentId],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/brain/clusters/${deploymentId}`)) as any
      return r.data as { embedding_run_id: string | null; clusters: ClusterAssignment[] }
    },
    enabled: !!deploymentId,
  })
}

export function useUmapCoords(deploymentId?: string) {
  return useQuery({
    queryKey: ['brain', 'umap', deploymentId],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/brain/umap/${deploymentId}`)) as any
      return r.data as { points: UmapPoint[]; count: number }
    },
    enabled: !!deploymentId,
  })
}

export function useOutliers(deploymentId?: string) {
  return useQuery({
    queryKey: ['brain', 'outliers', deploymentId],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/brain/outliers/${deploymentId}`)) as any
      return r.data as { outliers: Array<{ media_id: string; cluster_confidence: number | null }>; count: number }
    },
    enabled: !!deploymentId,
  })
}

export function useSimilarImages(mediaId: string | null, n = 20) {
  return useQuery({
    queryKey: ['brain', 'similar', mediaId, n],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/brain/similar/${mediaId}?n=${n}`)) as any
      return r.data as { media_id: string; results: SimilarHit[] }
    },
    enabled: !!mediaId,
  })
}

// ── Mutations ──────────────────────────────────────────────────────────────

export function useConfirmCluster(deploymentId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (args: {
      id: string
      taxon: { taxon_id?: string; scientific_name?: string; vernacular_name?: string }
    }) => {
      const r = (await apiClient.post(`/api/brain/clusters/${args.id}/confirm`, args.taxon)) as any
      return r.data as {
        cluster_assignment_id: string
        observations_created: number
        observation_ids: string[]
        /** Members a person had already labelled, left as they were. */
        skipped: number
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['brain', 'clusters', deploymentId] })
    },
  })
}

/** Undo a cluster confirm: its human rows go and the cluster reopens. */
export function useUnconfirmCluster(deploymentId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const r = await apiClient.post(`/api/brain/clusters/${id}/unconfirm`)
      return r.data as { cluster_assignment_id: string; observations_deleted: number; kept: number }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['brain', 'clusters', deploymentId] })
    },
  })
}

export interface MultiClusterResult {
  clusters: (ClusterAssignment & { model_name?: string })[]
  /** media_id → cluster_id for non-outlier members (drives grid grouping). */
  media_clusters: Record<string, number>
  outlier_media_ids: string[]
  model_groups: { model_name: string; deployment_ids: string[] }[]
}

export function useMultiClusters(deploymentIds: string[], minConfidence = 0) {
  return useQuery({
    queryKey: ['brain', 'clusters-multi', ...deploymentIds.sort(), minConfidence],
    queryFn: async () => {
      const r = (await apiClient.post('/api/brain/clusters/multi', {
        deployment_ids: deploymentIds,
        min_confidence: minConfidence,
      })) as any
      return r.data as MultiClusterResult
    },
    enabled: deploymentIds.length > 0,
  })
}

export function useEmbedDeployment() {
  return useMutation({
    mutationFn: async (deploymentId: string) => {
      const r = (await apiClient.post(`/api/brain/embed/${deploymentId}`, { mode: 'server' })) as any
      return r.data as { job_id: string; status: string }
    },
  })
}

/**
 * Reassign photos to a species, or flag them for an expert, through `/api/brain/review/batch`:
 * one request per 500 photos, each photo keeping one human row (03 #18). `skipped` counts the
 * photos the backend left alone (no write access, or gone).
 */
export function useBulkReview(deploymentId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (args: {
      mediaIds: string[]
      decision: 'reassign' | 'expert'
      taxon?: { taxon_id: string | null; scientific_name: string; vernacular_name: string | null }
    }) => {
      const body = args.decision === 'reassign' ? { decision: args.decision, ...args.taxon } : { decision: args.decision }
      return reviewInBatches(args.mediaIds, async ids =>
        (await apiClient.post('/api/brain/review/batch', { ...body, media_ids: ids })).data as ReviewBatchResult)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['brain', 'clusters', deploymentId] })
    },
  })
}
