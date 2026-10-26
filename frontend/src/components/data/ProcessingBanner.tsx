// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProcessingBanner: explains blank thumbnails and missing labels on the review grid. When an
// upload or AI run is still processing a deployment the user is looking at, the grid would
// otherwise show empty cards with no reason. Driven by the user's active jobs (GET /api/jobs),
// intersected with the deployments currently in view. A run that only makes thumbnails (an
// upload without the AI, or a Retry) says so, not that the AI is analysing.
import { Link } from 'react-router-dom'
import { ChartColumn, RefreshCw } from 'lucide-react'
import { useJobsList } from '../../hooks/useJobs'
import { onlyMakingThumbnails } from '../../lib/thumbnailRetry'
import { Banner, BannerProgress } from '../ui/Banner'
import { SMALL_BTN } from './gridStyles'

export function ProcessingBanner({ deployments }: { deployments: readonly { id: string; location_name: string | null }[] }) {
  const { data: jobs } = useJobsList()
  const viewIds = new Set(deployments.map(d => d.id))
  const names = new Map(deployments.map(d => [d.id, d.location_name || d.id.slice(0, 8)]))

  const active = (jobs ?? []).filter(
    j => (j.status === 'queued' || j.status === 'processing') && (j.deployment_ids ?? []).some(id => viewIds.has(id)),
  )
  if (active.length === 0) return null

  const depIds = [...new Set(active.flatMap(j => (j.deployment_ids ?? []).filter(id => viewIds.has(id))))]
  const depNames = depIds.map(id => names.get(id) || id.slice(0, 8))
  const shown = depNames.length <= 3 ? depNames.join(', ') : `${depNames.slice(0, 3).join(', ')} +${depNames.length - 3} more`
  const isOne = depIds.length === 1
  // Progress from the active AI job(s) (now reported per-deployment); the analysing job carries it.
  const pct = Math.round(Math.max(0, ...active.map(j => j.progress ?? 0)) * 100)
  // Insights takes the deployment parameter once per deployment.
  const insightsLink = `/insights?tab=reports${depIds.map(id => `&deployment=${encodeURIComponent(id)}`).join('')}`

  if (onlyMakingThumbnails(active)) {
    return (
      <Banner tone="info" icon={RefreshCw} footer={<BannerProgress pct={pct} />}>
        <strong>Making thumbnails {pct}%</strong>: {isOne ? 'deployment ' : 'deployments '}
        <strong>{shown}</strong>. The photos appear as they are ready.
      </Banner>
    )
  }

  return (
    <Banner
      tone="info"
      icon={RefreshCw}
      action={
        <Link to={insightsLink} style={{ ...SMALL_BTN, border: 'none', fontWeight: 600, fontSize: '0.8125rem' }}
          title="See species and detection numbers for these photos as they are analysed">
          <ChartColumn size={14} aria-hidden /> See stats so far
        </Link>
      }
      footer={<BannerProgress pct={pct} />}
    >
      <strong>Analysing {pct}%</strong>: the AI is processing {isOne ? 'deployment ' : 'deployments '}
      <strong>{shown}</strong>. Thumbnails and labels appear as they complete.
    </Banner>
  )
}
