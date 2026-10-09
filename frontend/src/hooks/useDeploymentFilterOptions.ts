import { useMemo } from 'react'
import { useProjectSelection } from './useProjectSelection'
import { deploymentFilterOptions, type FilterDeployment, type FilterOption } from '../lib/deploymentFilterOptions'

/** The deployment filter's options, labelled with the project name when several are in view (#207). */
export function useDeploymentFilterOptions(deployments: FilterDeployment[]): FilterOption[] {
  const { projects } = useProjectSelection()
  return useMemo(
    () => deploymentFilterOptions(deployments, new Map(projects.map(p => [p.id, p.name]))),
    [deployments, projects],
  )
}
