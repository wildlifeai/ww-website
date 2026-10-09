/* eslint-disable react-refresh/only-export-components */
/**
 * UploadContext, the global upload pipeline state.
 *
 * Holds everything that must outlive the /upload-data page (UploadFlow):
 * the multi-batch API loop, Drive-job polling and the progress dock, so an
 * upload keeps going while the user navigates away.
 *
 * Usage:
 *   const { startUpload, dockState, pipelineState, phase } = useUploadStore()
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { apiClient } from '../lib/apiClient'
import { registerLocalPreviews } from '../lib/localPreviewStore'
import { derivePhase, type UploadPhase } from '../lib/uploadPhase'
import { orderFilesBySession, planBatches } from '../lib/uploadPlanning'
import type { PipelineState, PipelineJob, LogEntry } from '../components/toolkit/PipelineStatusBox'

// Re-exported for existing consumers; implementations live in lib/ so they
// can be unit-tested without React or the API client.
export { derivePhase } from '../lib/uploadPhase'
export type { UploadPhase } from '../lib/uploadPhase'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type DockState = 'hidden' | 'minimised' | 'medium'

/** One just-uploaded file bound to its deployment, used to render optimistic grid cards. */
export interface PendingUpload {
  fileName: string
  deploymentId: string
}

/** Minimal deployment shape needed for coordinate fallback and stats. */
export interface UploadDeployment {
  id: string
  project_id: string
  name: string | null
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
}

/** The browser's "leave site?" prompt while batches are still sending. */
function warnWhileSending(e: BeforeUnloadEvent) {
  e.preventDefault()
}

export interface StartUploadOptions {
  /** Precise deployment ids the images belong to (for the annotations filter/redirect). */
  resolvedDeploymentIds?: string[]
  /** Per-file → deployment mapping, for the optimistic Annotations grid before DB rows exist. */
  pending?: PendingUpload[]
  /** Capture-session bindings from the triage step, one deployment per group of files. Files
   *  are ordered so each batch carries a single deployment, because assigned_deployment_id is
   *  one value per request. This is the only source of a deployment assignment. */
  sessionAssignments?: { deploymentId: string; indices: number[] }[]
  /** Run the AI pipeline + Wildlife Brain after upload (the server's default is true). */
  runAi?: boolean
}

interface UploadContextValue {
  // ── Pipeline ───────────────────────────────────────────────────────────────
  pipelineState: PipelineState
  phase: UploadPhase
  /** True while a batch loop is running or Drive jobs are still in-flight. */
  isActive: boolean

  // ── Actions ────────────────────────────────────────────────────────────────
  /** Kick off a multi-batch image upload and show the dock. */
  startUpload: (
    files: File[],
    paths: string[],
    uploadToDrive: boolean,
    deployments: UploadDeployment[],
    options?: StartUploadOptions,
  ) => Promise<void>
  clearUpload: () => void

  // ── Dock ───────────────────────────────────────────────────────────────────
  dockState: DockState
  setDockState: (s: DockState) => void

  /** Files still uploading, keyed to their deployment — powers the optimistic Annotations grid. */
  pendingUploads: PendingUpload[]
  /** When the current upload started (ms since epoch), or null; real rows created after it retire the optimistic cards. */
  pendingSince: number | null

  // ── WS5-T6: deployment IDs from the most recent upload ────────────────────
  /** IDs of deployments included in the last upload batch. */
  uploadedDeploymentIds: string[]
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

const EMPTY_PIPELINE: PipelineState = {
  totalFiles: 0,
  uploadedFiles: 0,
  failedFiles: 0,
  jobs: [],
  logs: [],
  lastUpdateTs: 0,
  uploadError: null,
}

// ─────────────────────────────────────────────────────────────────────────────
// Context
// ─────────────────────────────────────────────────────────────────────────────

const UploadContext = createContext<UploadContextValue | null>(null)

// ─────────────────────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────────────────────

const BATCH_SIZE = 10

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const [pipelineState, setPipelineState] = useState<PipelineState>(EMPTY_PIPELINE)
  const [dockState, setDockState] = useState<DockState>('hidden')
  const [uploadedDeploymentIds, setUploadedDeploymentIds] = useState<string[]>([])
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([])
  const [pendingSince, setPendingSince] = useState<number | null>(null)

  // Guards and tracking refs (don't need re-render)
  const busyRef = useRef(false)
  const lastSeenSeqRef = useRef<Record<string, number>>({})

  const phase = derivePhase(pipelineState)
  const isActive =
    phase === 'uploading' || phase === 'processing' || phase === 'stalled'

  // ── Clear ──────────────────────────────────────────────────────────────────
  const clearUpload = useCallback(() => {
    if (busyRef.current) return          // don't clear mid-upload
    setPipelineState(EMPTY_PIPELINE)
    setDockState('hidden')
    setUploadedDeploymentIds([])
    setPendingUploads([])
    setPendingSince(null)
    lastSeenSeqRef.current = {}
  }, [])

  // Once the pipeline is terminal every batch has registered its media rows, so the optimistic
  // grid cards are redundant (the real rows dedup them out): drop them. This runs during render
  // on the phase change rather than in an effect, so the cards never outlive the terminal render.
  const [lastPhase, setLastPhase] = useState(phase)
  if (phase !== lastPhase) {
    setLastPhase(phase)
    if (phase === 'completed' || phase === 'failed') { setPendingUploads([]); setPendingSince(null) }
  }

  // ── Job polling ────────────────────────────────────────────────────────────
  useEffect(() => {
    const { jobs } = pipelineState
    if (jobs.length === 0) return

    const incomplete = jobs.filter((j) =>
      ['queued', 'in_progress', 'processing', 'deferred'].includes(j.status),
    )
    if (incomplete.length === 0) return

    const timer = setInterval(async () => {
      try {
        const updates = await Promise.all(
          jobs.map(async (job): Promise<PipelineJob> => {
            if (
              ['completed', 'completed_with_errors', 'failed', 'skipped'].includes(job.status)
            )
              return job

            try {
              const res = await apiClient.get(`/api/jobs/${job.id}`)
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const j: any = res.data?.data ?? res.data ?? {}

              let status: string = j.status ?? job.status
              let progress: number = j.progress ?? job.progress

              if (progress >= 0.999 && ['in_progress', 'processing'].includes(status)) {
                status = 'completed'
                progress = 1.0
              }

              return {
                ...job,
                status,
                progress,
                error: j.error ? String(j.error) : job.error,
                message: j.message ?? job.message,
                updatedAt: j.updated_at ?? job.updatedAt,
                currentPhase: j.current_phase ?? job.currentPhase,
                summary: j.summary ?? job.summary,
                eventCount: j.event_count ?? job.eventCount ?? 0,
                // Transient — consumed below, stripped before storing in state
                _events: j.events ?? [],
              } as PipelineJob & { _events: unknown[] }
            } catch {
              return job
            }
          }),
        )

        let changed = false
        const logsToAdd: LogEntry[] = []
        // Jobs spawned by a running job (e.g. AI analysis offloaded to the GPU
        // worker) that the dock should chain onto so it keeps polling + the dock
        // stays open until the follow-on finishes.
        const childJobsToAdd: PipelineJob[] = []

        for (let i = 0; i < jobs.length; i++) {
          const prev = jobs[i]
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const next = updates[i] as any

          if (
            prev.status !== next.status ||
            prev.progress !== next.progress ||
            prev.message !== next.message ||
            (next.eventCount ?? 0) > (prev.eventCount ?? 0)
          ) {
            changed = true
          }

          // Structured event consumption (seq-based de-dup)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const events: any[] = next._events ?? []
          const prevSeq = lastSeenSeqRef.current[next.id] ?? -1
           
          const newEvents = events
            .sort((a: any, b: any) => a.seq - b.seq)
            .filter((e: any) => e.seq > prevSeq)

          if (newEvents.length > 0) {
            lastSeenSeqRef.current[next.id] = newEvents[newEvents.length - 1].seq
            changed = true

            for (const evt of newEvents) {
              let level: LogEntry['level'] = 'info'
              if (evt.type === 'file_success' || evt.type === 'phase_complete')
                level = 'success'
              if (evt.type === 'file_failure' || evt.type === 'stall_warning')
                level = 'error'
              if (evt.type === 'file_skip') level = 'warning'
              if (evt.type === 'heartbeat') level = 'warning'

              logsToAdd.push({
                ts: new Date(evt.timestamp).getTime() || Date.now(),
                level,
                message: evt.message,
              })

              // Chain onto a spawned follow-on job (e.g. offloaded AI analysis).
              const childId: string | undefined = evt.child_job_id
              if (
                childId &&
                !jobs.some((j) => j.id === childId) &&
                !childJobsToAdd.some((c) => c.id === childId)
              ) {
                childJobsToAdd.push({ id: childId, status: 'queued', progress: 0, fileCount: 0 })
              }
            }
          } else if (next.message && next.message !== prev.message && events.length === 0) {
            // Fallback for job types without structured events
            let level: LogEntry['level'] = 'info'
            if (
              next.status === 'completed' ||
              next.status === 'completed_with_errors'
            )
              level = 'success'
            if (next.status === 'failed') level = 'error'
            logsToAdd.push({ ts: Date.now(), level, message: next.message })
          }

          delete next._events
        }

        // De-duplicate simultaneous identical log messages
        const seen = new Set<string>()
        const deduped = logsToAdd.filter((l) => {
          if (seen.has(l.message)) return false
          seen.add(l.message)
          return true
        })

        if (changed || childJobsToAdd.length > 0) {
          setPipelineState((prev) => ({
            ...prev,
            // Append any newly-spawned follow-on jobs so the dock keeps polling
            // them; the effect re-runs on the new jobs list and the dock stays
            // open until every job (including the chained AI job) is terminal.
            jobs: [...updates, ...childJobsToAdd],
            logs: [...prev.logs, ...deduped],
            lastUpdateTs: Date.now(),
          }))
        }
      } catch (e) {
        console.error('[UploadContext] polling failed', e)
      }
    }, 2000)

    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineState.jobs])

  // ── startUpload ────────────────────────────────────────────────────────────
  const startUpload = useCallback(
    async (
      files: File[],
      paths: string[],
      uploadToDrive: boolean,
      deployments: UploadDeployment[],
      { resolvedDeploymentIds, pending, sessionAssignments, runAi = true }: StartUploadOptions = {},
    ): Promise<void> => {
      if (busyRef.current) return
      busyRef.current = true
      // A reload or tab close while the batch loop runs drops every batch not yet sent. The
      // upload page guards only its staged selection and unmounts long before the loop ends, so
      // the loop guards the tab itself until its last batch (#143). Server-side processing
      // carries on without the tab, so it is not guarded.
      window.addEventListener('beforeunload', warnWhileSending)

      // Order files by session so a batch never mixes deployments, and remember
      // which deployment each position belongs to.
      const { order, perFileDeployment } = orderFilesBySession(files.length, sessionAssignments)
      if (order) {
        files = order.map((i) => files[i])
        paths = order.map((i) => paths[i] ?? '')
      }

      // Deployment IDs for the post-upload annotations filter/redirect: prefer the precise set
      // the page resolved (folder-prefix matches ∪ manual assignment); fall back to all.
      setUploadedDeploymentIds(
        resolvedDeploymentIds && resolvedDeploymentIds.length ? resolvedDeploymentIds : deployments.map(d => d.id),
      )
      setPendingUploads(pending ?? [])
      setPendingSince(Date.now())

      // Instant thumbnails: mint object URLs from the user's own files now, so the
      // Annotations grid can show them within ~1s (before any server rendition).
      registerLocalPreviews(files)

      // Batch boundaries must never span two deployments — see lib/uploadPlanning.
      const batchPlan = planBatches(files.length, perFileDeployment, BATCH_SIZE)
      const totalBatches = batchPlan.length
      lastSeenSeqRef.current = {}

      // Initialise state, show dock
      setPipelineState({
        totalFiles: files.length,
        uploadedFiles: 0,
        failedFiles: 0,
        uploadError: null,
        jobs: [],
        logs: [
          {
            ts: Date.now(),
            level: 'info',
            message: `🚀 Starting upload for ${files.length} image${files.length !== 1 ? 's' : ''} (${totalBatches} batch${totalBatches !== 1 ? 'es' : ''})…`,
          },
        ],
        lastUpdateTs: Date.now(),
      })
      setDockState('medium')

      try {
        for (const [planIdx, plan] of batchPlan.entries()) {
          const i = plan.start
          const batchNum = planIdx + 1
          const batchEnd = plan.end
          const chunk = files.slice(plan.start, plan.end)
          const chunkPaths = paths.slice(plan.start, plan.end)

          setPipelineState((prev) => ({
            ...prev,
            logs: [
              ...prev.logs,
              {
                ts: Date.now(),
                level: 'info',
                message: `📦 Batch ${batchNum}/${totalBatches} — images ${i + 1}–${batchEnd}…`,
              },
            ],
            lastUpdateTs: Date.now(),
          }))

          const formData = new FormData()
          for (const f of chunk) formData.append('files', f)
          for (const p of chunkPaths) formData.append('paths', p)
          if (uploadToDrive) formData.append('upload_to_drive', 'true')
          formData.append('run_ai', String(runAi))
          // Every file in this batch shares one deployment by construction (see batchPlan).
          if (plan.assigned) formData.append('assigned_deployment_id', plan.assigned)

          try {
            const response = await apiClient.upload('/api/exif/parse', formData)
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const data: any = response.data ?? {}
            const driveInfo = data.drive_upload

            setPipelineState((prev) => {
              const logs = [...prev.logs]
              const jobs = [...prev.jobs]
              let uploadError = prev.uploadError ?? null
              // Files in this batch the server accepted but will never store (no Drive job).
              let failedInBatch = 0

              if (driveInfo) {
                // The server can refuse the whole storage step - Drive not
                // configured (GOOGLE_DRIVE_ENABLED unset), or not requested.
                // Images are then never stored AND never get a media row (the
                // drive job is what creates them), so this must read as a
                // failure rather than passing silently: production ran this
                // branch for weeks while the dock showed a green tick.
                if (driveInfo.enabled === false) {
                  const why = driveInfo.reason === 'server_disabled'
                    ? 'Image storage is not configured on this server, so nothing was saved. Contact an administrator.'
                    : `Image storage was not used (${driveInfo.reason ?? 'unknown reason'}), so nothing was saved.`
                  logs.push({ ts: Date.now(), level: 'error', message: `❌ ${why}` })
                  uploadError = uploadError || why
                }
                // Server blocked images whose deployment the user can't access (enforced
                // regardless of the client-side warning).
                if (driveInfo.blocked_deployments?.length) {
                  logs.push({
                    ts: Date.now(),
                    level: 'warning',
                    message: `🚫 ${driveInfo.blocked_deployments.length} deployment${driveInfo.blocked_deployments.length !== 1 ? 's' : ''} skipped — you don't have access to them.`,
                  })
                }
                if (driveInfo.status === 'skipped') {
                  const reason =
                    driveInfo.reason === 'no_files_stored'
                      ? 'images already exist (duplicates)'
                      : driveInfo.reason
                  logs.push({
                    ts: Date.now(),
                    level: 'warning',
                    message: `⏭️ Batch ${batchNum} skipped: ${reason}`,
                  })
                } else if (driveInfo.job_id) {
                  jobs.push({
                    id: driveInfo.job_id,
                    status: driveInfo.status || 'queued',
                    progress: 0,
                    fileCount: driveInfo.file_count || chunk.length,
                  })
                  if ((driveInfo.duplicates_skipped ?? 0) > 0) {
                    logs.push({
                      ts: Date.now(),
                      level: 'warning',
                      message: `⏭️ ${driveInfo.duplicates_skipped} already exist (skipped).`,
                    })
                  }
                  if ((driveInfo.file_count ?? 0) > 0) {
                    logs.push({
                      ts: Date.now(),
                      level: 'success',
                      message: `✅ Buffered. Drive sync queued for ${driveInfo.file_count} images.`,
                    })
                  }
                } else if (driveInfo.status === 'error') {
                  // The server took the files but could not start their Drive job (on the bench
                  // run: a dropped database connection while enqueueing). Nothing in this batch
                  // gets a media row, so count it as failed and say so in the dock; a log line
                  // alone left "2 photos missing" invisible.
                  const why = `Batch ${batchNum} was not saved: ${driveInfo.error || 'unknown error'}`
                  logs.push({ ts: Date.now(), level: 'error', message: `❌ ${why}` })
                  uploadError = uploadError || why
                  failedInBatch = batchEnd - i
                }
              } else if (!uploadToDrive) {
                logs.push({
                  ts: Date.now(),
                  level: 'success',
                  message: `✅ Images ${i + 1}–${batchEnd} extracted.`,
                })
              }

              // Count this batch's files, don't jump to the absolute position:
              // batchEnd re-absorbs any earlier batch that failed, so uploaded +
              // failed exceeded the total and derivePhase went terminal while
              // later batches were still sending (dropping the optimistic grid
              // early and overstating "X of Y photos" in the dock).
              return {
                ...prev,
                uploadedFiles: prev.uploadedFiles + (batchEnd - i) - failedInBatch,
                failedFiles: (prev.failedFiles ?? 0) + failedInBatch,
                jobs,
                logs,
                uploadError,
                lastUpdateTs: Date.now(),
              }
            })

            // enabled:false is a server-wide condition (Drive unconfigured or
            // not requested), not a property of this batch - every remaining
            // batch would upload its bytes just to get the same refusal. Stop
            // here and count the unsent files as failed so the dock's totals
            // stay honest.
            if (driveInfo && driveInfo.enabled === false) {
              const remaining = files.length - batchEnd
              if (remaining > 0) {
                setPipelineState((prev) => ({
                  ...prev,
                  logs: [
                    ...prev.logs,
                    {
                      ts: Date.now(),
                      level: 'error',
                      message: `⏹️ Stopping — not sending the remaining ${remaining} photo${remaining === 1 ? '' : 's'}: the server refuses image storage, so they would be discarded too.`,
                    },
                  ],
                  failedFiles: (prev.failedFiles ?? 0) + remaining,
                  lastUpdateTs: Date.now(),
                }))
              }
              break
            }
          } catch (e: unknown) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = e as any
            const msg =
              err?.response?.data?.detail ??
              err?.response?.data?.error?.message ??
              err?.message ??
              String(err)
            const batchCount = plan.end - plan.start
            setPipelineState((prev) => ({
              ...prev,
              logs: [
                ...prev.logs,
                {
                  ts: Date.now(),
                  level: 'error',
                  message: `❌ Batch ${batchNum} failed: ${msg}`,
                },
              ],
              // Count the batch as FAILED - never as uploaded. Advancing
              // uploadedFiles here made a total failure render as
              // "N of N photos · Pipeline Complete 100%".
              failedFiles: (prev.failedFiles ?? 0) + batchCount,
              // Surface the first error where the user can see it without
              // expanding the technical log
              uploadError: prev.uploadError || msg,
              lastUpdateTs: Date.now(),
            }))
          }
        }
      } finally {
        busyRef.current = false
        window.removeEventListener('beforeunload', warnWhileSending)
      }
    },
    [],
  )

  return (
    <UploadContext.Provider
      value={{
        pipelineState,
        phase,
        isActive,
        startUpload,
        clearUpload,
        dockState,
        setDockState,
        uploadedDeploymentIds,
        pendingUploads,
        pendingSince,
      }}
    >
      {children}
    </UploadContext.Provider>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Hook
// ─────────────────────────────────────────────────────────────────────────────

export function useUploadStore(): UploadContextValue {
  const ctx = useContext(UploadContext)
  if (!ctx) throw new Error('useUploadStore must be used within <UploadProvider>')
  return ctx
}

