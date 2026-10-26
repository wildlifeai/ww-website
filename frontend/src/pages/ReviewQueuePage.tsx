import { Navigate, useParams } from 'react-router-dom'

/**
 * The Review Queue is retired (UI audit decision 6): the photo viewer in the grid does the same
 * job with boxes, zoom and the species picker. Old `/review/:deployment_id` links land on that
 * deployment's photos.
 */
export function ReviewQueuePage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  return <Navigate to={deployment_id ? `/annotations?deployment=${deployment_id}` : '/annotations'} replace />
}
