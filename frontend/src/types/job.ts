/**
 * Shared TypeScript types for the async job / pipeline system.
 *
 * These mirror the backend Pydantic schemas in `app/schemas/job.py`.
 * The frontend uses them for deterministic UI rendering — no string parsing.
 */

/* ── Enums (as union types) ───────────────────────────────────────── */

export type ProgressPhase = 'upload' | 'download' | 'drive_upload' | 'cleanup' | 'ai_pipeline'

export type EventType =
  | 'job_started'
  | 'phase_start'
  | 'phase_complete'
  | 'progress'
  | 'file_success'
  | 'file_failure'
  | 'file_skip'
  | 'folder_created'
  | 'heartbeat'
  | 'stall_warning'

export type JobStatusValue =
  | 'queued'
  | 'processing'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'

/* ── Models ───────────────────────────────────────────────────────── */

export interface ProgressEvent {
  seq: number
  type: EventType
  phase: ProgressPhase
  timestamp: string
  current?: number
  total?: number
  file_index?: number
  filename?: string
  message: string
  batch_index?: number
  job_id?: string
  /** A spawned follow-on job (e.g. AI offloaded to the GPU worker) the dock should chain onto. */
  child_job_id?: string
}

export interface ProgressSummary {
  total: number
  downloaded: number
  uploaded: number
  skipped: number
  failed: number
  started_at: string | null
}

export interface JobInfo {
  job_id: string
  status: JobStatusValue
  progress: number
  created_at: string
  updated_at: string | null
  result_url: string | null
  error: string | null
  message: string | null
  current_phase: ProgressPhase | null
  summary: ProgressSummary | null
  events: ProgressEvent[]
  event_count: number
}

/** Light summary row for the processing-history list (GET /api/jobs). */
export interface JobSummary {
  job_id: string
  status: JobStatusValue
  kind: string | null
  label: string | null
  /** Deployments this job touches — drives the Annotations "being processed" banner. */
  deployment_ids: string[]
  progress: number | null
  summary: ProgressSummary | null
  error: string | null
  created_at: string
  updated_at: string | null
  event_count: number
}

/** One page of GET /api/jobs; `next_before` reads the next page and is null on the last. */
export interface JobsPage {
  jobs: JobSummary[]
  next_before: string | null
}
