import { Navigate } from 'react-router-dom'

/**
 * /upload/logs, retired (UI audit decision 3, 01 E2): Processing history keeps every upload's
 * job log, and this page only held the current upload until a reload. Kept as a redirect so the
 * upload dock's links still land somewhere; the route and this file go once App.tsx drops them.
 */
export function UploadLogsPage() {
  return <Navigate to="/processing" replace />
}
