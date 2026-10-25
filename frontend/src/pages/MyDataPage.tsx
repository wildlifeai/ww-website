import { Navigate, useLocation } from 'react-router-dom'

/**
 * /my-data-legacy, retired (UI audit decision 3): Insights, Annotations and Settings replaced
 * every tab. Kept as a redirect, with the query string, as /my-data does; the route and this
 * file go once App.tsx drops them.
 */
export function MyDataPage() {
  const { search } = useLocation()
  return <Navigate to={`/insights${search}`} replace />
}
