import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/apiClient'

export interface MediaRow {
  id: string
  file_name: string | null
  timestamp: string | null
  thumbnail_url: string | null
  preview_url: string | null
  original_url: string | null
}

/**
 * Paginated media for a deployment with pre-resolved CDN URLs (Media Registry).
 * A URL is null when the photo has no rendition yet and its original is private:
 * show a placeholder, never the auth-gated image proxy (#305).
 */
export function useMediaRegistry(deploymentId?: string, page = 1, pageSize = 200) {
  return useQuery({
    queryKey: ['mediaRegistry', deploymentId, page, pageSize],
    queryFn: async () => {
      const r = (await apiClient.get(`/api/media/registry/${deploymentId}?page=${page}&page_size=${pageSize}`)) as any
      return r.data as { media: MediaRow[]; page: number; page_size: number; count: number }
    },
    enabled: !!deploymentId,
  })
}
