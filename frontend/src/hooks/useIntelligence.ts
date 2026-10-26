import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'

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
  /** False when the call failed: the active-learning/QA feature flag is off (the router is not mounted). */
  enabled: boolean
  n_compared: number
  matches: number
  precision: number | null
  /** Deployments with at least one photo compared, most compared first. */
  perDeployment: QaDeploymentResult[]
}

const QA_OFF: ProjectQa = { enabled: false, n_compared: 0, matches: 0, precision: null, perDeployment: [] }

/** The project's AI-vs-human agreement in one call, `/api/qa/report/project/{id}` (UI audit 03 #19). */
export function useProjectQa(projectId?: string, enabled = true) {
  return useQuery({
    queryKey: ['qa', 'project', projectId],
    queryFn: async (): Promise<ProjectQa> => {
      try {
        const r = (await apiClient.get(`/api/qa/report/project/${projectId}`)) as any
        const m = r.data as { n_compared: number; matches: number; precision: number | null; per_deployment: QaDeploymentResult[] }
        return { enabled: true, n_compared: m.n_compared, matches: m.matches, precision: m.precision, perDeployment: m.per_deployment }
      } catch {
        return QA_OFF
      }
    },
    enabled: !!projectId && enabled,
  })
}
