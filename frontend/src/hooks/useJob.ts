import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'
import type { JobInfo } from '../types/job'

export type { JobInfo }

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: async (): Promise<JobInfo> => {
      const response = await apiClient.get(`/api/jobs/${jobId}`)
      const data = response.data?.data ?? response.data
      return data as JobInfo
    },
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'completed' || status === 'completed_with_errors' || status === 'failed') return false
      return 2000 // Poll every 2s
    },
  })
}
