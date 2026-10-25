// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'

export interface DeploymentProject {
  projectId: string | null
  /** Latitude and longitude are both set: iNaturalist publishing needs them. */
  hasLocation: boolean
}

/**
 * The project a deployment belongs to, for the role checks on a page, and whether it has a
 * location. Read once per deployment.
 */
export function useDeploymentProject(deploymentId?: string) {
  return useQuery({
    queryKey: ['deployment-project', deploymentId],
    queryFn: async (): Promise<DeploymentProject> => {
      const { data, error } = await supabase
        .from('deployments')
        .select('project_id, latitude, longitude')
        .eq('id', deploymentId as string)
        .maybeSingle()
      if (error) throw new Error(error.message)
      return {
        projectId: (data?.project_id as string | undefined) ?? null,
        hasLocation: data?.latitude != null && data?.longitude != null,
      }
    },
    enabled: !!deploymentId,
    // The location can be set on Insights meanwhile; the project never changes.
    staleTime: 60_000,
  })
}
