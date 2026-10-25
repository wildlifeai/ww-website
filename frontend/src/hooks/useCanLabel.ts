// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useCanLabel: may the signed-in user label photos of this deployment (UI audit decision 1)?
// True for project_member and above (`useProjectRoles().canWrite`) on the deployment's project,
// while the deployment is live. `loading` is true until both the roles and the deployment are
// known: render no write control and no "view only" note until then. The deployment's project is
// read once per deployment and cached for ten minutes, not per photo (03 #4).
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../config/supabase'
import { useProjectRoles } from './useProjectRoles'

interface DeploymentScope {
  project_id: string
  deleted_at: string | null
}

async function deploymentScope(deploymentId: string): Promise<DeploymentScope | null> {
  const { data, error } = await supabase
    .from('deployments').select('project_id, deleted_at').eq('id', deploymentId).maybeSingle()
  if (error) throw new Error(error.message)
  return data as DeploymentScope | null
}

export function useCanLabel(deploymentId: string | null | undefined): { canLabel: boolean; loading: boolean } {
  const roles = useProjectRoles()
  const scope = useQuery({
    queryKey: ['deployment-scope', deploymentId],
    queryFn: () => deploymentScope(deploymentId!),
    enabled: !!deploymentId,
    staleTime: 10 * 60_000,
  })
  const loading = roles.loading || (!!deploymentId && scope.isPending)
  const live = scope.data && !scope.data.deleted_at ? scope.data : null
  return { canLabel: !loading && !!live && roles.canWrite(live.project_id), loading }
}
