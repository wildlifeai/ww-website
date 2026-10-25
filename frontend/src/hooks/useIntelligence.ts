import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import { supabase } from '../config/supabase'
import { settleWithLimit } from '../lib/settleWithLimit'

export interface SpeciesCoverage {
  scientific_name: string
  count: number
  under_represented: boolean
}

export interface DatasetHealth {
  project_id: string
  deployments: number
  species: SpeciesCoverage[]
  species_count: number
  review_funnel: Record<string, number>
  outlier_rate: number | null
  total_observations: number
}

export function useDatasetHealth(projectId?: string) {
  return useQuery({
    queryKey: ['intelligence', 'health', projectId],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/intelligence/health/${projectId}`)) as any
      return r.data as DatasetHealth
    },
    enabled: !!projectId,
  })
}

export interface ConservationAlert {
  id: string
  alert_type: string
  severity: 'info' | 'warning' | 'critical'
  deployment_id: string | null
  details: Record<string, any> | null
  first_seen: string | null
}

export function useAlerts(projectId?: string) {
  return useQuery({
    queryKey: ['intelligence', 'alerts', projectId],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/intelligence/alerts/${projectId}`)) as any
      return r.data as { alerts: ConservationAlert[]; count: number }
    },
    enabled: !!projectId,
  })
}

// ── AN-9: AI-vs-human agreement (QA) aggregated across a project ─────────────

export interface QaDeploymentResult {
  deployment_id: string
  location_name: string | null
  n_compared: number
  matches: number
  precision: number | null
}

export interface ProjectQa {
  /** False when the active-learning/QA feature flag is off (every call 404/disabled). */
  enabled: boolean
  n_compared: number
  matches: number
  precision: number | null
  perDeployment: QaDeploymentResult[]
}

/** QA report calls in flight at once until a project-level route exists (UI audit 03 #19). */
const QA_CONCURRENCY = 4

/**
 * Fan out the per-deployment QA report (`/api/qa/report/{id}`) across the
 * project's deployments, four at a time, and aggregate into a single precision
 * proxy. Individual disabled/failed calls are skipped; if none succeed,
 * `enabled` is false.
 */
export function useProjectQa(projectId?: string, enabled = true) {
  return useQuery({
    queryKey: ['qa', 'project', projectId],
    queryFn: async (): Promise<ProjectQa> => {
      const { data: deps } = await supabase
        .from('deployments')
        .select('id, location_name')
        .eq('project_id', projectId!)
        .is('deleted_at', null)

      const list = (deps ?? []) as { id: string; location_name: string | null }[]
      let anyEnabled = false
      const perDeployment: QaDeploymentResult[] = []

      // A disabled or failed call is skipped (the settled result is ignored).
      await settleWithLimit(list, QA_CONCURRENCY, async d => {
        const r = (await apiClient.get(`/api/qa/report/${d.id}`)) as any
        anyEnabled = true
        const m = r.data
        perDeployment.push({
          deployment_id: d.id,
          location_name: d.location_name,
          n_compared: m.n_compared ?? 0,
          matches: m.matches ?? 0,
          precision: m.precision ?? null,
        })
      })

      const n = perDeployment.reduce((a, p) => a + p.n_compared, 0)
      const matches = perDeployment.reduce((a, p) => a + p.matches, 0)
      return {
        enabled: anyEnabled,
        n_compared: n,
        matches,
        precision: n > 0 ? matches / n : null,
        perDeployment: perDeployment
          .filter(p => p.n_compared > 0)
          .sort((a, b) => b.n_compared - a.n_compared),
      }
    },
    enabled: !!projectId && enabled,
  })
}
