/* eslint-disable react-hooks/set-state-in-effect */
/**
 * AnnotationsPage — /annotations
 *
 * WS5: MediaBrowser with a branded Ribbon (deployment/species/status filters,
 *      thumbnail size), StatusBadge overlays, advanced-settings popup
 *      (date range, day/night), and URL-based deployment
 *      pre-selection for upload→annotations handoff (WS5-T6).
 */
import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useFilledTimezones } from '../hooks/useFilledTimezones'
import { MediaBrowser } from '../components/data/MediaBrowser'
import { NoProjectSelected } from '../components/common/NoProjectSelected'
import { supabase } from '../config/supabase'

interface Deployment {
  id: string
  project_id: string
  location_name: string | null
  timezone?: string | null
  latitude?: number | null
  longitude?: number | null
  deployment_start?: string | null
}

export function AnnotationsPage() {
  const { user } = useAuth()
  const { queryProjectIds, noProjectSelected } = useProjectSelection()
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const [loading, setLoading] = useState(true)
  const shown = useFilledTimezones(deployments)

  // WS5-T6: read ?deployment=<id> placed by the upload dock "View Annotations" link
  const [searchParams] = useSearchParams()
  const initialDeploymentId = searchParams.get('deployment') ?? undefined
  const initialSpecies = searchParams.get('species') ?? undefined

  useEffect(() => {
    if (!user || !queryProjectIds) return
    let cancelled = false
    setLoading(true)

    // Try selecting the timezone column; if it isn't deployed yet, fall back to the
    // base columns so the page keeps working (capture times then use browser-local time).
    const runQuery = (cols: string) => supabase
      .from('deployments')
      .select(cols)
      .is('deleted_at', null)
      .order('created_at', { ascending: false })
      .in('project_id', queryProjectIds)

    ;(async () => {
      const withTz = await runQuery('id, project_id, location_name, deployment_start, latitude, longitude, timezone')
      const data = withTz.error
        ? (await runQuery('id, project_id, location_name, deployment_start')).data
        : withTz.data
      if (cancelled) return
      setDeployments((data as unknown as Deployment[]) || [])
      setLoading(false)
    })()

    return () => { cancelled = true }
  }, [user, queryProjectIds])

  if (noProjectSelected) return <NoProjectSelected />

  return (
    <div>
      {loading ? (
        <p style={{ opacity: 0.5 }}>Loading deployments…</p>
      ) : (
        <MediaBrowser
          deployments={shown}
          initialDeploymentId={initialDeploymentId}
          initialSpecies={initialSpecies}
        />
      )}
    </div>
  )
}

