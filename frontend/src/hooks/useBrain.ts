import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { settleWithLimit } from '../lib/settleWithLimit'

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
      return r.data as { cluster_assignment_id: string; observations_created: number }
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

// ── Active learning / review queue (Phase 8) ───────────────────────────────

export interface ReviewQueueItem {
  media_id: string
  active_learning_score: number | null
  cluster_id: number
  is_outlier: boolean
  cluster_confidence: number | null
  ai_label: string | null
  ai_confidence: number | null
  human_label: string | null
}

export function useReviewQueue(deploymentId?: string, limit = 50) {
  return useQuery({
    queryKey: ['brain', 'review-queue', deploymentId, limit],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/brain/review-queue/${deploymentId}?limit=${limit}`)) as any
      return r.data as { queue: ReviewQueueItem[]; count: number }
    },
    enabled: !!deploymentId,
  })
}

/** Review calls in flight at once while reassign still posts one call per photo (03 #18). */
const REVIEW_CONCURRENCY = 4

/**
 * Reassign photos to a species, or flag them for an expert: one `/api/brain/review` call per
 * photo, each adding a human row, at most four at a time. A batch route would make it one call.
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
      const results = await settleWithLimit(args.mediaIds, REVIEW_CONCURRENCY, mid =>
        apiClient.post(`/api/brain/review/${mid}`, body))
      const ok = results.filter(r => r.status === 'fulfilled').length
      return { ok, failed: results.length - ok }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['brain', 'clusters', deploymentId] })
      qc.invalidateQueries({ queryKey: ['brain', 'review-queue', deploymentId] })
    },
  })
}
