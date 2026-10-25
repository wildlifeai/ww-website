/**
 * ProgressDock, fixed bottom-right upload progress indicator.
 *
 * Three visual states driven by `dockState` from UploadContext:
 *
 *   hidden     Nothing rendered.
 *   minimised  Compact pill: semaphore dot + label + expand and dismiss.
 *   medium     Card with full PipelineStatusBox + controls.
 *
 * UploadContext folds the card to the pill once every batch is sent and the
 * server is processing, and opens it again on the result (UI audit 01 E3).
 * The finished card offers one next step: the results on Insights on success
 * (the page has already moved to Review), or the job's log in Processing history
 * on failure. Dismiss stays reachable while stalled.
 */
import { ArrowRight, Minus, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useUploadStore } from '../../contexts/UploadContext'
import { PipelineStatusBox } from '../toolkit/PipelineStatusBox'

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

const DOCK_BASE: React.CSSProperties = {
  position: 'fixed',
  bottom: '1.25rem',
  right: '1.25rem',
  zIndex: 300,
  boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
  borderRadius: 'var(--radius)',
}

const BTN_ICON: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  padding: '0.2rem 0.35rem',
  borderRadius: '4px',
  color: 'var(--text-muted)',
  lineHeight: 1,
  flexShrink: 0,
}

const PULSE = `
  @keyframes dock-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.75); }
  }
`

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function ProgressDock() {
  const { pipelineState, phase, dockState, setDockState, clearUpload, uploadedDeploymentIds } = useUploadStore()

  if (dockState === 'hidden') return null

  const color = semaphoreColor(phase)
  // Active job's current backend phase, used to distinguish Drive sync vs AI analysis
  const activeJobPhase = pipelineState.jobs.find(
    (j) => j.status === 'processing' || j.status === 'in_progress',
  )?.currentPhase
  const label = dockLabel(phase, pipelineState.totalFiles, activeJobPhase)
  const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
  const isDone = phase === 'completed' || phase === 'failed'
  const hasErrors = pipelineState.jobs.some(
    (j) => j.status === 'failed' || j.status === 'completed_with_errors',
  )
  // Dismiss is offered whenever finished or stalled, so a job that hangs never traps
  // the dock; never while batches are still sending (clearing is refused then).
  const canDismiss = isDone || phase === 'stalled'

  // The results: Insights' Reports tab, narrowed to the deployment when only one was uploaded.
  const resultsLink = uploadedDeploymentIds.length === 1
    ? `/insights?tab=reports&deployment=${encodeURIComponent(uploadedDeploymentIds[0])}`
    : '/insights?tab=reports'

  // The failed job's row in Processing history, which keeps its log after a reload.
  const failedJob = pipelineState.jobs.find((j) => j.status === 'failed')
    ?? pipelineState.jobs.find((j) => j.status === 'completed_with_errors')
  const logLink = failedJob ? `/processing?job=${encodeURIComponent(failedJob.id)}` : null
  // The one next step once it has finished: the results on a clean run, else the log (when a job has one).
  const nextStep = !isDone ? null : phase === 'completed' && !hasErrors ? 'results' : logLink ? 'log' : null

  const dot = (size: number) => (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        backgroundColor: color,
        flexShrink: 0,
        animation: isActive ? 'dock-pulse 1.5s ease-in-out infinite' : undefined,
      }}
    />
  )

  const dismiss = canDismiss && (
    <button type="button" style={BTN_ICON} onClick={clearUpload} title="Dismiss" aria-label="Dismiss">
      <X size={16} aria-hidden="true" />
    </button>
  )

  // ── Minimised pill ─────────────────────────────────────────────────────────
  if (dockState === 'minimised') {
    return (
      <div
        style={{
          ...DOCK_BASE,
          display: 'flex',
          alignItems: 'center',
          gap: '0.25rem',
          padding: '0.25rem 0.375rem 0.25rem 0.5rem',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)',
        }}
      >
        <button
          type="button"
          onClick={() => setDockState('medium')}
          title="Expand upload progress"
          style={{
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            padding: '0.25rem 0.375rem', background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-color)', fontSize: '0.8125rem', fontWeight: 500, whiteSpace: 'nowrap',
          }}
        >
          {dot(9)}
          {label}
        </button>
        {dismiss}
        <style>{PULSE}</style>
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
        // minimise/dismiss controls) off the top of the screen. The body scrolls instead.
        maxHeight: 'calc(100vh - 2.5rem)',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'var(--bg-color)',
        border: '1px solid var(--border)',
        overflow: 'hidden',
      }}
    >
      {/* Card header, pinned, always reachable */}
      <div style={{
        flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.625rem 0.875rem',
        borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--surface)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {dot(8)}
          <span style={{ fontWeight: 600, fontSize: '0.875rem' }}>{label}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
          <button
            type="button"
            style={BTN_ICON}
            onClick={() => setDockState('minimised')}
            title="Minimise"
            aria-label="Minimise"
          >
            <Minus size={16} aria-hidden="true" />
          </button>
          {dismiss}
        </div>
      </div>

      {/* Pipeline status box, the only scrollable region, so the header + footer CTA stay put */}
      <div style={{ padding: '0.75rem', overflowY: 'auto', flex: 1, minHeight: 0 }}>
        <PipelineStatusBox state={pipelineState} phase={phase} />
      </div>

      {/* The one next step once it has finished */}
      {nextStep && (
        <div style={{
          flexShrink: 0,
          padding: '0.625rem 0.875rem',
          borderTop: '1px solid var(--border)',
          backgroundColor: 'var(--surface)',
          fontSize: '0.8125rem',
        }}>
          {nextStep === 'results' ? (
            <Link
              to={resultsLink}
              style={{ color: 'var(--primary)', fontWeight: 600, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
              // Keep the upload state + logs around (minimised) instead of clearing,
              // so the user can still see what happened after navigating away.
              onClick={() => setDockState('minimised')}
            >
              See the results <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : logLink && (
            <Link
              to={logLink}
              style={{ color: 'var(--error, #f44336)', fontWeight: 600, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}
              onClick={() => setDockState('minimised')}
            >
              Open the log in Processing history <ArrowRight size={14} aria-hidden="true" />
            </Link>
          )}
        </div>
      )}

      <style>{PULSE}</style>
    </div>
  )
}
