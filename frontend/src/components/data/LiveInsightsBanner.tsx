// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * LiveInsightsBanner: shown on the Insights page while a just-started upload is still being
 * classified by the AI. It says the reports below rest on a partial set of automatic predictions
 * (and how far along), and links to Annotations to review them. Reads the global upload store, so
 * it appears wherever the upload was started. Drawn with the shared Banner (UI audit 02 #28).
 */
import { Link } from 'react-router-dom'
import { Camera, Cpu } from 'lucide-react'
import { useUploadStore } from '../../contexts/UploadContext'
import { aiAnalysisStatus } from '../toolkit/pipelineProgress'
import { Banner, BannerProgress } from '../ui/Banner'

export function LiveInsightsBanner() {
  const { uploadedDeploymentIds, phase, pipelineState } = useUploadStore()

  // Nothing uploaded this session, or the dock was dismissed: no banner.
  if (uploadedDeploymentIds.length === 0 || phase === 'idle') return null

  const ai = aiAnalysisStatus(pipelineState)
  const done = phase === 'completed'
  const failed = phase === 'failed'

  // % of images classified by the AI: the analysis job's aggregate progress once it has
  // started. Before that (still uploading or queued) there is no AI job yet: "queued".
  const aiStarted = !!ai || done
  const pct = done ? 100 : ai ? Math.round(ai.progress * 100) : 0

  const annotationsLink = uploadedDeploymentIds.length === 1
    ? `/annotations?deployment=${uploadedDeploymentIds[0]}`
    : '/annotations'

  const headline = failed
    ? 'AI analysis failed for this upload. See the upload logs.'
    : done
      ? 'AI analysis complete: every photo is classified.'
      : aiStarted
        ? `Live results, based on ${pct}% of photos classified by the AI. Numbers are predictions until reviewed.`
        : 'AI analysis queued. Results appear here as photos are classified.'

  return (
    <Banner
      tone={failed ? 'error' : done ? 'success' : 'info'}
      icon={failed || done ? undefined : Cpu}
      action={
        <Link
          to={annotationsLink}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.82rem', fontWeight: 600, color: 'var(--primary)', textDecoration: 'none', whiteSpace: 'nowrap' }}
          title="Open the photos in Media to review and correct the AI's identifications"
        >
          <Camera size={14} aria-hidden /> Review in Media
        </Link>
      }
      footer={!failed && aiStarted ? <BannerProgress pct={pct} tone={done ? 'success' : 'info'} /> : undefined}
    >
      <strong>{headline}</strong>
    </Banner>
  )
}
