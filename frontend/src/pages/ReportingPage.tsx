import { Navigate, useParams } from 'react-router-dom'

/**
 * /reporting/:deployment_id, folded into Insights (UI audit decision 5): one deployment's results
 * are the Reports tab filtered to it, and the CamtrapDP export is on Toolkit. Kept as a redirect
 * so links and bookmarks still work; the route and this file go once App.tsx links to Insights.
 */
export function ReportingPage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const params = new URLSearchParams({ tab: 'reports' })
  if (deployment_id) params.set('deployment', deployment_id)
  return <Navigate to={`/insights?${params}`} replace />
}
