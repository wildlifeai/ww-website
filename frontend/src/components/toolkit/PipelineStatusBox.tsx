import { useRef, useState, useEffect } from 'react'
import { Info, TriangleAlert } from 'lucide-react'
import type { ProgressPhase, ProgressSummary } from '../../types/job'
import { aiAnalysisStatus } from './pipelineProgress'

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
  /** Files whose batch failed. Counted separately so a failed run can never
   *  reach "N of N photos" and read as a success (it used to: the batch catch
   *  advanced uploadedFiles by the whole batch). */
  failedFiles?: number
  jobs: PipelineJob[]
  logs: LogEntry[]
  lastUpdateTs: number
  /** Set when staging/enqueue failed before any job was created (e.g. Azure buffer
   *  unreachable). Drives the failed phase + issue banner so the run can't read as
   *  a false success. */
  uploadError?: string | null
}

/* ── Helpers ──────────────────────────────────────────────────────── */

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  if (m > 0) return `${m}m ${s.toString().padStart(2, '0')}s`
  return `${s}s`
}

/* ── Stage model ──────────────────────────────────────────────────── */

type StepStatus = 'pending' | 'active' | 'done' | 'warn' | 'error'

interface StepView {
  key: string
  label: string
  detail: string
  status: StepStatus
  progress: number | null
}

interface StepsResult {
  steps: StepView[]
  issue: { level: 'warn' | 'error'; text: string } | null
}

const STEP_COLOR: Record<StepStatus, string> = {
  pending: 'var(--text-muted)',
  active: 'var(--primary, #3b82f6)',
  done: 'var(--success, #4CAF50)',
  warn: 'var(--warning)',
  error: 'var(--error, #f44336)',
}
const STEP_ICON: Record<StepStatus, string> = { pending: '', active: '', done: '✓', warn: '!', error: '✕' }

const _TERMINAL = ['completed', 'completed_with_errors', 'failed', 'skipped']

/**
 * Collapse the raw PipelineState into the three stages a user actually cares about —
 * Upload → Save to Google Drive → AI analysis — with a plain-language issue summary.
 * Everything is derived from data the dock already has (uploadedFiles, the drive jobs'
 * summaries, and the AI job's progress); no new backend surface.
 */
function deriveSteps(state: PipelineState, phase: string): StepsResult {
  const total = state.totalFiles
  const driveJobs = state.jobs.filter((j) => j.fileCount > 0)
  const aiJobs = state.jobs.filter((j) => j.fileCount === 0)
  const done = phase === 'completed'

  // 1 · Upload (browser → server)
  // Failed files are counted separately and NEVER as uploaded, so a run that
  // failed can't display as complete.
  const upFailed = state.failedFiles ?? 0
  const upSettled = state.uploadedFiles + upFailed
  const upDone = (done && upFailed === 0) || (total > 0 && state.uploadedFiles >= total)
  const uploadStep: StepView = {
    key: 'upload',
    label: 'Uploaded',
    detail:
      total === 0 ? 'Preparing…'
      : upFailed > 0 ? `${state.uploadedFiles} of ${total} photos · ${upFailed} failed`
      : `${Math.min(state.uploadedFiles, total)} of ${total} photos`,
    status: upFailed > 0 && upSettled >= total ? 'error'
      : total > 0 && upDone ? 'done'
      : 'active',
    progress: total > 0 ? upSettled / total : null,
  }

  // 2 · Save to Google Drive
  const dt = driveJobs.reduce(
    (a, j) => {
      if (j.summary) {
        a.uploaded += j.summary.uploaded ?? 0
        a.skipped += j.summary.skipped ?? 0
        a.failed += j.summary.failed ?? 0
      }
      return a
    },
    { uploaded: 0, skipped: 0, failed: 0 },
  )
  const driveFiles = driveJobs.reduce((s, j) => s + j.fileCount, 0)
  const driveProg = driveFiles > 0
    ? driveJobs.reduce((s, j) => s + Math.max(0, Math.min(1, j.progress || 0)) * j.fileCount, 0) / driveFiles
    : 0
  // A drive job can fail (crash / network) *before* it writes a summary, so dt.failed stays 0 —
  // fall back to the job status so a hard failure still shows as an error, not "done".
  const driveFailed = driveJobs.some((j) => j.status === 'failed')
  const driveTerm = driveJobs.length > 0 && driveJobs.every((j) => _TERMINAL.includes(j.status))
  let driveStatus: StepStatus
  if (state.uploadError && driveJobs.length === 0) driveStatus = 'error'
  else if (driveJobs.length === 0) driveStatus = done ? 'done' : upDone ? 'active' : 'pending'
  else if (done || driveTerm) driveStatus = driveFailed || dt.failed > 0 ? 'error' : dt.skipped > 0 ? 'warn' : 'done'
  else driveStatus = 'active'
  const driveDetail =
    driveStatus === 'pending' ? 'Waiting for upload'
    : dt.failed > 0 ? `${dt.uploaded} saved · ${dt.failed} failed`
    : driveFailed ? 'Saving to Google Drive failed'
    : dt.skipped > 0 ? `${dt.uploaded} saved · ${dt.skipped} skipped`
    : driveStatus === 'active' ? `${dt.uploaded} saved so far…`
    : `${dt.uploaded} saved`
  const driveStep: StepView = {
    key: 'drive', label: 'Saved to Google Drive', detail: driveDetail,
    status: driveStatus, progress: driveStatus === 'active' ? driveProg : null,
  }

  // 3 · AI analysis
  const ai = aiAnalysisStatus(state)
  const aiFailed = aiJobs.some((j) => j.status === 'failed')
  let aiStatus: StepStatus
  if (aiJobs.length === 0) aiStatus = done ? 'done' : 'pending'
  else if (ai?.active) aiStatus = 'active'
  else if (aiFailed) aiStatus = 'error'
  else aiStatus = 'done'
  const aiDetail =
    aiStatus === 'pending' ? 'Waiting for upload to finish'
    : aiStatus === 'error' ? 'Analysis failed — see technical details'
    : aiStatus === 'done' ? 'Species identified'
    : 'Detecting animals, then identifying species'
  const aiStep: StepView = {
    key: 'ai', label: 'Analysing with AI', detail: aiDetail,
    status: aiStatus, progress: aiStatus === 'active' && ai ? ai.progress : null,
  }

  let issue: StepsResult['issue'] = null
  if (state.uploadError) {
    issue = { level: 'error', text: state.uploadError }
  } else if (driveFailed || dt.failed > 0) {
    issue = {
      level: 'error',
      text: dt.failed > 0
        ? `${dt.failed} photo${dt.failed !== 1 ? 's' : ''} couldn't be saved to Google Drive. See technical details.`
        : 'Saving to Google Drive failed. See technical details.',
    }
  } else if (aiFailed) {
    issue = { level: 'error', text: 'AI analysis hit an error on some deployments. Your photos are safe — you can retry from Processing history.' }
  } else if (dt.skipped > 0) {
    issue = { level: 'warn', text: `${dt.skipped} photo${dt.skipped !== 1 ? 's' : ''} skipped — already in your library. Nothing to do.` }
  }

  return { steps: [uploadStep, driveStep, aiStep], issue }
}

/* ── Stage row ────────────────────────────────────────────────────── */

function StepRow({ step, last }: { step: StepView; last: boolean }) {
  const color = STEP_COLOR[step.status]
  const isActive = step.status === 'active'
  const isPending = step.status === 'pending'
  return (
    <div style={{ display: 'flex', gap: '0.6rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <span style={{
          width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '0.72rem', fontWeight: 700, color: isPending ? 'var(--text-muted)' : '#fff',
          background: isPending ? 'var(--border)' : color,
          border: isPending ? '1px solid var(--border)' : 'none',
        }}>
          {isActive
            ? <span className="step-spin" style={{ width: 10, height: 10, border: '2px solid rgba(255,255,255,0.45)', borderTopColor: '#fff', borderRadius: '50%' }} />
            : STEP_ICON[step.status]}
        </span>
        {!last && <span style={{ flex: 1, width: 2, minHeight: 12, background: 'var(--border)', margin: '2px 0' }} />}
      </div>
      <div style={{ paddingBottom: last ? 0 : '0.7rem', flex: 1, minWidth: 0, color: isPending ? 'var(--text-muted)' : undefined }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.82rem', fontWeight: 500 }}>{step.label}</span>
          {isActive && step.progress != null && (
            <span style={{ fontSize: '0.75rem', color, fontWeight: 600 }}>{Math.round(step.progress * 100)}%</span>
          )}
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 1 }}>{step.detail}</div>
        {isActive && step.progress != null && (
          <div style={{ height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden', marginTop: 5 }}>
            <div style={{ width: `${Math.round(step.progress * 100)}%`, height: '100%', background: color, transition: 'width 0.3s ease-out' }} />
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Derive the section title deterministically from the pipeline phase,
 * never by parsing message strings.
 */
function deriveSectionTitle(state: PipelineState, phase: string): string {
  if (phase === 'uploading') {
    return `Uploading & analysing images… (${state.uploadedFiles}/${state.totalFiles})`
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
      return `Done with issues: ${totals.uploaded} uploaded, ${totals.skipped} skipped, ${totals.failed} failed`
    }
    if (totals.uploaded + totals.skipped > 0) {
      return `Done: ${totals.uploaded} uploaded${totals.skipped > 0 ? `, ${totals.skipped} skipped` : ''}`
    }
    return 'Pipeline Complete'
  }
  if (phase === 'failed') return 'Pipeline Failed'
  if (phase === 'stalled') return 'Still working… (taking longer than usual)'

  // Processing — phase 2: if the offloaded AI-analysis job is running, surface ITS
  // progress (it long outlives the upload, so this is what the user is waiting on).
  const ai = aiAnalysisStatus(state)
  if (ai?.active) {
    return `Analysing images… ${Math.round(ai.progress * 100)}%`
  }

  // Processing — use the current phase from the most advanced active job
  const activeJob = state.jobs.find(j => j.status === 'processing')
  const jobPhase = activeJob?.currentPhase

  switch (jobPhase) {
    case 'download':     return `Downloading ${state.totalFiles} images from Azure…`
    case 'drive_upload': return `Uploading ${state.totalFiles} images to Google Drive…`
    case 'cleanup':      return `Cleaning up temporary files…`
    case 'ai_pipeline':  return `Running AI analysis…`
    default:             return `Processing ${state.totalFiles} images…`
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
  const [progressState, setProgressState] = useState<{ phaseKey: 'upload' | 'ai'; maxPercent: number }>({
    phaseKey: 'upload',
    maxPercent: 0,
  })
  const logContainerRef = useRef<HTMLDivElement>(null)
  const prevLogCountRef = useRef(0)
  const [elapsed, setElapsed] = useState(0)
  const [showLog, setShowLog] = useState(false)
  const startTsRef = useRef(0)

  // Elapsed timer — ticks every second while pipeline is active
  useEffect(() => {
    const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
    if (!isActive) return

    startTsRef.current = state.logs.length > 0 ? state.logs[0].ts : Date.now()
    const timer = setInterval(() => {
      setElapsed(Date.now() - startTsRef.current)
    }, 1000)

    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // Weight by fileCount, not raw job count. AI-analysis children have fileCount 0 and
  // drive the separate phase below, so they don't dilute the upload progress here.
  const jobProgress = totalFilesInJobs > 0
      ? state.jobs.reduce((sum, j) => sum + Math.max(0, Math.min(1, j.progress || 0)) * j.fileCount, 0) / totalFilesInJobs
      : 0

  const uploadPhasePct = state.jobs.some(j => j.fileCount > 0) ? (uploadProgress * 0.3 + jobProgress * 0.7) : uploadProgress

  // Two-phase bar: while the offloaded AI job runs, the bar tracks ITS progress instead of
  // pinning at 100% (the upload is already done). Reset the monotonic floor when crossing
  // into the AI phase so the analysis bar fills from 0 rather than the upload's ~100%.
  const aiStatus = aiAnalysisStatus(state)
  const inAiPhase = !!aiStatus?.active
  const phaseKey: 'upload' | 'ai' = inAiPhase ? 'ai' : 'upload'
  const rawPercent = inAiPhase && aiStatus ? aiStatus.progress : uploadPhasePct
  const calculatedPct = Math.round(rawPercent * 100)

  // Track the monotonic max per phase in state (not a ref). Adjusting state *during* render is
  // the React-supported way to remember a value derived from props — and unlike mutating a ref
  // it stays correct under Strict/Concurrent rendering (a discarded render's setState is dropped
  // too). It converges: no setState fires once maxPercent >= the current value within a phase.
  if (progressState.phaseKey !== phaseKey) {
    // Crossed into a new phase → reset the floor so the bar fills from 0 again.
    setProgressState({ phaseKey, maxPercent: calculatedPct })
  } else if (calculatedPct > progressState.maxPercent) {
    setProgressState({ phaseKey, maxPercent: calculatedPct })
  }

  // Use fresh values for THIS render — state may lag one render behind a setState above.
  const flooredPct = progressState.phaseKey === phaseKey
    ? Math.max(progressState.maxPercent, calculatedPct)
    : calculatedPct
  // Never claim 100% until the whole pipeline (incl. AI) is terminal.
  const percent = phase === 'completed' ? 100 : Math.min(flooredPct, 99)

  const isActive = phase === 'uploading' || phase === 'processing' || phase === 'stalled'
  const eta = isActive ? estimateEta(state, elapsed) : null
  const { steps, issue } = deriveSteps(state, phase)

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
                  backgroundColor: phase === 'stalled' ? 'var(--warning)' : 'var(--primary, #3b82f6)',
                  animation: 'pipeline-pulse 1.5s ease-in-out infinite',
                  flexShrink: 0,
                }}
              />
            )}
            {deriveSectionTitle(state, phase)}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.8125rem' }}>
          {isActive && (
            <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontWeight: 400 }}>
              {formatElapsed(elapsed)}
              {eta && <span style={{ marginLeft: '0.5rem' }}>• {eta} remaining</span>}
            </span>
          )}
          <span>{percent}%</span>
        </span>
      </div>

      <div style={{ height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
        <div
          style={{
            width: `${percent}%`,
            height: '100%',
            background: phase === 'failed' ? 'var(--error)' : 'var(--primary)',
            transition: 'width 0.3s ease-out',
          }}
        />
      </div>

      {/* ── Stages: Upload → Save to Drive → AI analysis ── */}
      <div style={{ marginTop: '0.9rem' }}>
        {steps.map((s, i) => (
          <StepRow key={s.key} step={s} last={i === steps.length - 1} />
        ))}
      </div>

      {/* Plain-language issue summary (replaces decoding "Batch N: Done (with errors)") */}
      {issue && (
        <div style={{
          marginTop: '0.4rem', padding: '0.5rem 0.6rem', borderRadius: 4, fontSize: '0.76rem',
          display: 'flex', gap: '0.45rem', alignItems: 'flex-start',
          background: issue.level === 'error' ? 'rgba(244,67,54,0.08)' : 'rgba(255,152,0,0.10)',
          color: issue.level === 'error' ? 'var(--error, #f44336)' : '#e65100',
        }}>
          {issue.level === 'error'
            ? <TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0 }} />
            : <Info size={14} aria-hidden="true" style={{ flexShrink: 0 }} />}
          <span>{issue.text}</span>
        </div>
      )}

      {/* Technical log — collapsed by default so it doesn't drown the summary */}
      <button
        onClick={() => setShowLog(v => !v)}
        style={{ marginTop: '0.6rem', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-muted)', fontSize: '0.75rem' }}
      >
        {showLog ? '▾ Hide technical details' : '▸ Show technical details'}
      </button>
      {showLog && (
        <div
          ref={logContainerRef}
          style={{
            marginTop: '0.4rem', fontSize: '0.72rem', maxHeight: '150px', overflowY: 'auto',
            fontFamily: 'monospace', background: 'var(--border)', padding: '0.5rem', borderRadius: 4,
          }}
        >
          {state.logs.map((l, i) => {
            let color = 'inherit'
            if (l.level === 'error') color = 'var(--error)'
            if (l.level === 'success') color = 'var(--success, #4CAF50)'
            if (l.level === 'warning') color = 'var(--warning)'
            return (
              <div key={`${l.ts}-${i}`} style={{ opacity: 0.9, color, marginBottom: '0.2rem' }}>
                <span style={{ color: 'var(--text-muted)', marginRight: '0.5rem' }}>
                  {new Date(l.ts).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
                {l.message}
              </div>
            )
          })}
          {state.logs.length === 0 && <div style={{ color: 'var(--text-muted)' }}>Waiting for pipeline to start…</div>}
        </div>
      )}

      {/* Animations */}
      <style>{`
        @keyframes pipeline-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.75); }
        }
        @keyframes step-spin { to { transform: rotate(360deg); } }
        .step-spin { animation: step-spin 0.8s linear infinite; display: inline-block; }
      `}</style>
    </div>
  )
}
