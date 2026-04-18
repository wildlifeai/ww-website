import { useRef, useState, useEffect } from 'react'
import type { ProgressPhase, ProgressSummary } from '../../types/job'

export type LogEntry = {
  ts: number
  level: 'info' | 'success' | 'warning' | 'error'
  message: string
}

export type PipelineJob = {
  id: string
  status: string
  progress: number
  fileCount: number
  error?: string
  message?: string
  updatedAt?: string
  currentPhase?: ProgressPhase | null
  summary?: ProgressSummary | null
  eventCount?: number
}

export type PipelineState = {
  totalFiles: number
  uploadedFiles: number
  jobs: PipelineJob[]
  logs: LogEntry[]
  lastUpdateTs: number
}

/* ── Helpers ──────────────────────────────────────────────────────── */

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  if (m > 0) return `${m}m ${s.toString().padStart(2, '0')}s`
  return `${s}s`
}

function jobStatusLabel(status: string): string {
  switch (status) {
    case 'queued': return 'Queued'
    case 'processing':
    case 'in_progress': return 'Processing'
    case 'completed': return 'Done'
    case 'completed_with_errors': return 'Done (with errors)'
    case 'failed':
    case 'error': return 'Failed'
    case 'skipped': return 'Skipped'
    case 'deferred': return 'Deferred'
    default: return status
  }
}

function jobStatusColor(status: string): string {
  switch (status) {
    case 'completed': return 'var(--success, #4CAF50)'
    case 'completed_with_errors': return 'var(--warning, #FF9800)'
    case 'failed':
    case 'error': return 'var(--error, #f44336)'
    case 'processing':
    case 'in_progress': return 'var(--primary, #3b82f6)'
    case 'skipped': return 'var(--warning, #FF9800)'
    default: return 'var(--text-secondary, #888)'
  }
}

/**
 * Derive the section title deterministically from the pipeline phase —
 * never by parsing message strings.
 */
function deriveSectionTitle(state: PipelineState, phase: string): string {
  if (phase === 'uploading') {
    return `📤 Uploading & analysing images… (${state.uploadedFiles}/${state.totalFiles})`
  }
  if (phase === 'completed') {
    const hasErrors = state.jobs.some(j => j.status === 'completed_with_errors')
    const totals = state.jobs.reduce(
      (acc, j) => {
        if (j.summary) {
          acc.uploaded += j.summary.uploaded
          acc.skipped += j.summary.skipped
          acc.failed += j.summary.failed
        }
        return acc
      },
      { uploaded: 0, skipped: 0, failed: 0 },
    )
    if (hasErrors) {
      return `⚠️ Done with issues — ${totals.uploaded} uploaded, ${totals.skipped} skipped, ${totals.failed} failed`
    }
    if (totals.uploaded + totals.skipped > 0) {
      return `✅ Done — ${totals.uploaded} uploaded${totals.skipped > 0 ? `, ${totals.skipped} skipped` : ''}`
    }
    return '✅ Pipeline Complete'
  }
  if (phase === 'failed') return '❌ Pipeline Failed'
  if (phase === 'stalled') return '⏳ Still working… (taking longer than usual)'

  // Processing — use the current phase from the most advanced active job
  const activeJob = state.jobs.find(j => j.status === 'processing')
  const jobPhase = activeJob?.currentPhase

  switch (jobPhase) {
    case 'download':     return `📥 Downloading ${state.totalFiles} images from Azure…`
    case 'drive_upload': return `☁️ Uploading ${state.totalFiles} images to Google Drive…`
    case 'cleanup':      return `🧹 Cleaning up temporary files…`
    default:             return `🔄 Processing ${state.totalFiles} images…`
  }
}

/**
 * Per-phase ETA — only estimates within the current phase to avoid
 * misleading jumps when phase speeds differ.
 */
function estimateEta(state: PipelineState, elapsedMs: number): string | null {
  const activeJob = state.jobs.find(j => j.status === 'processing')
  if (!activeJob?.summary || !activeJob.currentPhase) return null

  const s = activeJob.summary
  let completedInPhase = 0
  const totalInPhase = s.total

  switch (activeJob.currentPhase) {
    case 'download':
      completedInPhase = s.downloaded + s.failed
      break
    case 'drive_upload':
      completedInPhase = s.uploaded + s.skipped + s.failed - (s.total - s.downloaded)
      if (completedInPhase < 0) completedInPhase = s.uploaded + s.skipped
      break
    default:
      return null
  }

  if (completedInPhase <= 0 || totalInPhase <= 0 || completedInPhase >= totalInPhase) return null

  // Simple linear estimate within this phase
  const msPerItem = elapsedMs / completedInPhase
  const remaining = totalInPhase - completedInPhase
  const etaSec = Math.ceil((msPerItem * remaining) / 1000)

  if (etaSec < 5) return 'a few seconds'
  if (etaSec < 60) return `~${etaSec}s`
  return `~${Math.ceil(etaSec / 60)}m`
}

/* ── Component ────────────────────────────────────────────────────── */

export function PipelineStatusBox({ state, phase }: { state: PipelineState; phase: string }) {
  const prevPercentRef = useRef(0)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const prevLogCountRef = useRef(0)
  const [elapsed, setElapsed] = useState(0)
  const startTsRef = useRef(Date.now())

  // Elapsed timer — ticks every second while pipeline is active
  useEffect(() => {
    const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
    if (!isActive) return

    startTsRef.current = state.logs.length > 0 ? state.logs[0].ts : Date.now()
    const timer = setInterval(() => {
      setElapsed(Date.now() - startTsRef.current)
    }, 1000)

    return () => clearInterval(timer)
  }, [phase, state.logs.length > 0 ? state.logs[0].ts : 0])

  // Auto-scroll log container when new logs arrive
  useEffect(() => {
    if (state.logs.length > prevLogCountRef.current && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
    prevLogCountRef.current = state.logs.length
  }, [state.logs.length])

  const uploadProgress = state.totalFiles > 0 ? (state.uploadedFiles / state.totalFiles) : 0
  const totalFilesInJobs = state.jobs.reduce((sum, j) => sum + j.fileCount, 0)

  // Weight by fileCount, not raw job count
  const jobProgress = totalFilesInJobs > 0
      ? state.jobs.reduce((sum, j) => sum + Math.max(0, Math.min(1, j.progress || 0)) * j.fileCount, 0) / totalFilesInJobs
      : 0

  const rawPercent = state.jobs.length > 0 ? (uploadProgress * 0.3 + jobProgress * 0.7) : uploadProgress

  // Clamp the value using useRef to ensure progress never jumps backwards
  const percent = Math.max(prevPercentRef.current, Math.round(rawPercent * 100))
  prevPercentRef.current = percent

  const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
  const eta = isActive ? estimateEta(state, elapsed) : null

  return (
    <div className="card" style={{ marginTop: '1rem', padding: '1rem', border: '1px solid var(--border)' }}>
      <div style={{ marginBottom: '0.75rem', fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            {/* Pulsing dot to show system is alive */}
            {isActive && (
              <span
                style={{
                  display: 'inline-block',
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  backgroundColor: phase === 'stalled' ? 'var(--warning, #FF9800)' : 'var(--primary, #3b82f6)',
                  animation: 'pipeline-pulse 1.5s ease-in-out infinite',
                  flexShrink: 0,
                }}
              />
            )}
            {deriveSectionTitle(state, phase)}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.8125rem' }}>
          {isActive && (
            <span style={{ opacity: 0.6, fontSize: '0.75rem', fontWeight: 400 }}>
              ⏱ {formatElapsed(elapsed)}
              {eta && <span style={{ marginLeft: '0.5rem' }}>• {eta} remaining</span>}
            </span>
          )}
          <span>{percent}%</span>
        </span>
      </div>

      <div style={{ height: 6, background: 'var(--surface-sunken)', borderRadius: 3, overflow: 'hidden' }}>
        <div
          style={{
            width: `${percent}%`,
            height: '100%',
            background: phase === 'failed' ? 'var(--error)' : 'var(--primary)',
            transition: 'width 0.3s ease-out',
          }}
        />
      </div>

      {/* Per-job mini progress — only shown when there are multiple active jobs */}
      {state.jobs.length > 1 && (
        <div style={{
          marginTop: '0.75rem',
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          fontSize: '0.7rem',
          fontFamily: 'monospace',
        }}>
          {state.jobs.map((job, idx) => (
            <span
              key={job.id}
              style={{
                padding: '0.2rem 0.5rem',
                borderRadius: '4px',
                background: 'var(--surface-sunken)',
                color: jobStatusColor(job.status),
                border: `1px solid ${jobStatusColor(job.status)}22`,
              }}
            >
              Batch {idx + 1}: {job.status === 'completed' || job.status === 'completed_with_errors' || job.status === 'failed' || job.status === 'skipped'
                ? jobStatusLabel(job.status)
                : `${Math.round((job.progress || 0) * 100)}%`
              }
            </span>
          ))}
        </div>
      )}

      <div
        ref={logContainerRef}
        style={{
          marginTop: '0.75rem',
          fontSize: '0.8rem',
          maxHeight: '180px',
          overflowY: 'auto',
          fontFamily: 'monospace',
          background: 'var(--surface-sunken)',
          padding: '0.5rem',
          borderRadius: '4px'
        }}
      >
        {state.logs.map((l, i) => {
           let color = 'inherit'
           if (l.level === 'error') color = 'var(--error)'
           if (l.level === 'success') color = 'var(--success, #4CAF50)'
           if (l.level === 'warning') color = 'var(--warning, #FF9800)'

           return (
            <div key={`${l.ts}-${i}`} style={{ opacity: 0.9, color, marginBottom: '0.25rem' }}>
              <span style={{ opacity: 0.5, marginRight: '0.5rem' }}>
                {new Date(l.ts).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              {l.message}
            </div>
          )
        })}
        {state.logs.length === 0 && <div style={{ opacity: 0.5 }}>Waiting for pipeline to start...</div>}
      </div>

      {/* Pulse animation */}
      <style>{`
        @keyframes pipeline-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.75); }
        }
      `}</style>
    </div>
  )
}
