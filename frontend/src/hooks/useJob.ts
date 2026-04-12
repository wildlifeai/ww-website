import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'

export interface JobStatus {
  job_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  progress: number
  created_at: string
  result_url?: string
  error?: string
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: async (): Promise<JobStatus> => {
      const response = await apiClient.get(`/api/jobs/${jobId}`)
      return response.data
    },
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'completed' || status === 'failed') return false
      return 2000 // Poll every 2s
    },
  })
}
