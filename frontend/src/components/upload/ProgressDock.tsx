/**
 * ProgressDock — fixed bottom-right upload progress indicator.
 *
 * Three visual states driven by `dockState` from UploadContext:
 *
 *   hidden     Nothing rendered.
 *   minimised  Compact pill: semaphore dot + label + expand button.
 *   medium     Card with full PipelineStatusBox + controls.
 *
 * "Full logs" navigates to /upload/logs (a dedicated page).
 *
 * Completion behaviour:
 *   success → automatically shows a "View Annotations" link in the dock.
 *   failure → shows a "View Logs" link.
 *   The user dismisses the dock manually via the × button.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useUploadStore } from '../../contexts/UploadContext'
import { PipelineStatusBox } from '../toolkit/PipelineStatusBox'
import { supabase } from '../../config/supabase'
import { fetchLiveObservations } from '../../lib/liveObservations'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function semaphoreColor(phase: string): string {
  switch (phase) {
    case 'completed': return 'var(--success, #4CAF50)'
    case 'failed':    return 'var(--error, #f44336)'
    case 'stalled':   return 'var(--warning, #FF9800)'
    default:          return 'var(--primary, #3b82f6)'
  }
}

function dockLabel(phase: string, totalFiles: number, activeJobPhase?: string | null): string {
  switch (phase) {
    case 'uploading':  return `Uploading ${totalFiles} image${totalFiles !== 1 ? 's' : ''}…`
    case 'processing':
      if (activeJobPhase === 'ai_pipeline') return 'Running AI analysis…'
      if (activeJobPhase === 'cleanup')     return 'Cleaning up…'
      return `Syncing ${totalFiles} image${totalFiles !== 1 ? 's' : ''} to Drive…`
    case 'stalled':    return 'Still working…'
    case 'completed':  return 'Upload & analysis complete'
    case 'failed':     return 'Upload failed'
    default:           return 'Upload'
  }
}

function fmtHour(h: number): string {
  const ampm = h < 12 ? 'am' : 'pm'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return `${h12}${ampm}`
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadSummaryLine — one-line headline of what the AI found in this upload, so
// the user gets the gist without leaving the page (species · detections · peak).
// ─────────────────────────────────────────────────────────────────────────────

interface Summary { species: number; detections: number; peakHour: number | null }

function UploadSummaryLine({ deploymentIds }: { deploymentIds: string[] }) {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const key = deploymentIds.join(',')

  useEffect(() => {
    if (deploymentIds.length === 0) { setLoading(false); return }
    let cancelled = false
    setLoading(true)
    // Live photos only: a deleted photo's detections leave the summary (#198).
    fetchLiveObservations(supabase, {
      columns: 'scientific_name, observation_type',
      mediaColumns: 'timestamp',
      requirePhoto: true,
      filter: q => q.in('deployment_id', deploymentIds).eq('source_type', 'ai'),
    })
      .then(({ data, error }) => {
        if (cancelled) return
        if (error) { setSummary(null); setLoading(false); return }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const rows = (data ?? []) as any[]
        const species = new Set(rows.map(r => r.scientific_name).filter(Boolean)).size
        const detections = rows.filter(r => r.observation_type && r.observation_type !== 'blank').length
        const hourCounts = new Array(24).fill(0)
        let any = false
        for (const r of rows) {
          const m = Array.isArray(r.media) ? r.media[0] : r.media
          const ts = m?.timestamp
          if (!ts) continue
          const h = new Date(ts).getHours()
          if (!Number.isNaN(h)) { hourCounts[h]++; any = true }
        }
        const peakHour = any ? hourCounts.indexOf(Math.max(...hourCounts)) : null
        setSummary({ species, detections, peakHour })
        setLoading(false)
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (loading) return <span style={{ fontSize: '0.78rem', opacity: 0.6 }}>Summarising results…</span>
  if (!summary) return null

  if (summary.detections === 0) {
    return <span style={{ fontSize: '0.8125rem', opacity: 0.8 }}>📊 No animals detected in this upload</span>
  }
  const parts = [
    `${summary.species} species`,
    `${summary.detections} detection${summary.detections !== 1 ? 's' : ''}`,
  ]
  if (summary.peakHour != null) parts.push(`peak ${fmtHour(summary.peakHour)}`)
  return (
    <span style={{ fontSize: '0.8125rem', fontWeight: 600 }}>
      📊 {parts.join(' · ')}
    </span>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function ProgressDock() {
  const { pipelineState, phase, dockState, setDockState, clearUpload, uploadedDeploymentIds } = useUploadStore()
  const navigate = useNavigate()
  const prevPhaseRef = useRef<string>('')

  // Auto-expand from minimised to medium when upload starts
  useEffect(() => {
    if (
      dockState === 'minimised' &&
      (phase === 'uploading' || phase === 'processing')
    ) {
      // Keep minimised if user explicitly minimised
    }
  }, [phase, dockState])

  // When phase transitions to completed/failed, auto-expand if still minimised
  useEffect(() => {
    const prev = prevPhaseRef.current
    prevPhaseRef.current = phase

    if (
      (phase === 'completed' || phase === 'failed') &&
      prev !== 'completed' &&
      prev !== 'failed' &&
      dockState === 'minimised'
    ) {
      setDockState('medium')
    }

    // Auto-navigate to annotations on clean success (no failed jobs)
    if (
      phase === 'completed' &&
      prev !== 'completed' &&
      !pipelineState.jobs.some((j) => j.status === 'failed' || j.status === 'completed_with_errors')
    ) {
      // Don't force-navigate — show the link. Less disruptive.
    }
  }, [phase]) // eslint-disable-line react-hooks/exhaustive-deps

  if (dockState === 'hidden') return null

  const color = semaphoreColor(phase)
  // Active job's current backend phase — used to distinguish Drive sync vs AI analysis
  const activeJobPhase = pipelineState.jobs.find(
    (j) => j.status === 'processing' || j.status === 'in_progress',
  )?.currentPhase
  const label = dockLabel(phase, pipelineState.totalFiles, activeJobPhase)
  const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
  const isDone = phase === 'completed' || phase === 'failed'
  const hasErrors = pipelineState.jobs.some(
    (j) => j.status === 'failed' || j.status === 'completed_with_errors',
  )

  // WS5-T6: smart link — deep-link to the exact deployment when only one was uploaded
  const annotationsLink = uploadedDeploymentIds.length === 1
    ? `/annotations?deployment=${uploadedDeploymentIds[0]}`
    : '/annotations'

  // Results/insights — species diversity + activity summaries. For a single
  // deployment, the per-deployment Reporting page (real species × time charts);
  // otherwise the project-wide Insights → Reports tab.
  const insightsLink = uploadedDeploymentIds.length === 1
    ? `/reporting/${uploadedDeploymentIds[0]}`
    : '/insights?tab=reports'

  // ── Shared styles ──────────────────────────────────────────────────────────
  const DOCK_BASE: React.CSSProperties = {
    position: 'fixed',
    bottom: '1.25rem',
    right: '1.25rem',
    zIndex: 300,
    boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
    borderRadius: 'var(--radius)',
  }

  const BTN_ICON: React.CSSProperties = {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    padding: '0.2rem 0.35rem',
    borderRadius: '4px',
    color: 'var(--text-color)',
    fontSize: '0.875rem',
    opacity: 0.65,
    lineHeight: 1,
    flexShrink: 0,
  }

  // ── Minimised pill ─────────────────────────────────────────────────────────
  if (dockState === 'minimised') {
    return (
      <div
        style={{
          ...DOCK_BASE,
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.5rem 0.875rem',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onClick={() => setDockState('medium')}
        title="Expand upload progress"
      >
        {/* Semaphore dot */}
        <span
          style={{
            display: 'inline-block',
            width: 9,
            height: 9,
            borderRadius: '50%',
            backgroundColor: color,
            flexShrink: 0,
            animation: isActive ? 'dock-pulse 1.5s ease-in-out infinite' : undefined,
          }}
        />
        <span style={{ fontSize: '0.8125rem', fontWeight: 500, whiteSpace: 'nowrap' }}>
          {label}
        </span>
        <button
          style={BTN_ICON}
          onClick={(e) => { e.stopPropagation(); clearUpload() }}
          title="Dismiss"
        >
          ✕
        </button>
        <style>{`
          @keyframes dock-pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50%       { opacity: 0.4; transform: scale(0.75); }
          }
        `}</style>
      </div>
    )
  }

  // ── Medium card ────────────────────────────────────────────────────────────
  return (
    <div
      style={{
        ...DOCK_BASE,
        width: '360px',
        // Cap to the viewport so a tall pipeline log never pushes the header (and its
        // minimise/expand/dismiss controls) off the top of the screen. The body scrolls instead.
        maxHeight: 'calc(100vh - 2.5rem)',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'var(--bg-color)',
        border: '1px solid var(--border)',
        overflow: 'hidden',
      }}
    >
      {/* Card header — pinned, always reachable */}
      <div style={{
        flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.625rem 0.875rem',
        borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--surface)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span
            style={{
              display: 'inline-block',
              width: 8, height: 8, borderRadius: '50%',
              backgroundColor: color, flexShrink: 0,
              animation: isActive ? 'dock-pulse 1.5s ease-in-out infinite' : undefined,
            }}
          />
          <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{label}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
          <button
            style={BTN_ICON}
            onClick={() => navigate('/upload/logs')}
            title="View full logs"
          >
            ⤢
          </button>
          <button
            style={BTN_ICON}
            onClick={() => setDockState('minimised')}
            title="Minimise"
          >
            −
          </button>
          {/* Dismiss is available whenever finished OR stalled — so a job that
              hangs (e.g. an upload started mid-processing) never traps the dock. */}
          {(isDone || phase === 'stalled') && (
            <button
              style={BTN_ICON}
              onClick={clearUpload}
              title="Dismiss"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Pipeline status box — the only scrollable region, so the header + footer CTA stay put */}
      <div style={{ padding: '0.75rem', overflowY: 'auto', flex: 1, minHeight: 0 }}>
        <PipelineStatusBox state={pipelineState} phase={phase} />
      </div>

      {/* Completion CTA */}
      {isDone && (
        <div style={{
          flexShrink: 0,
          padding: '0.625rem 0.875rem',
          borderTop: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column', gap: '0.5rem',
          backgroundColor: 'var(--surface)',
          fontSize: '0.8125rem',
        }}>
          {/* One-line headline of what the AI found in this upload */}
          {phase === 'completed' && !hasErrors && uploadedDeploymentIds.length > 0 && (
            <UploadSummaryLine deploymentIds={uploadedDeploymentIds} />
          )}
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          {phase === 'completed' && !hasErrors && (
            <>
              {/* See the AI results straight away — species diversity + activity. */}
              <Link
                to={insightsLink}
                style={{ color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}
                onClick={() => setDockState('minimised')}
              >
                📊 See Insights →
              </Link>
              <Link
                to={annotationsLink}
                style={{ color: 'var(--text-color)', fontWeight: 600, textDecoration: 'none', opacity: 0.75 }}
                // Keep the upload state + logs around (minimised) instead of clearing,
                // so the user can still see what happened after navigating away.
                onClick={() => setDockState('minimised')}
              >
                🏷️ Review &amp; label
              </Link>
            </>
          )}
          {(phase === 'failed' || hasErrors) && (
            <Link
              to="/upload/logs"
              style={{ color: 'var(--error, #f44336)', fontWeight: 600, textDecoration: 'none' }}
            >
              View full logs →
            </Link>
          )}
          <button
            style={{
              marginLeft: 'auto', background: 'none', border: 'none',
              cursor: 'pointer', fontSize: '0.8125rem',
              color: 'var(--text-color)', opacity: 0.6,
            }}
            onClick={clearUpload}
          >
            Dismiss
          </button>
          </div>
        </div>
      )}

      <style>{`
        @keyframes dock-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.75); }
        }
      `}</style>
    </div>
  )
}
