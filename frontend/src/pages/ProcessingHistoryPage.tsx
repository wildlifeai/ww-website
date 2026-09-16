// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProcessingHistoryPage — /processing (opened from the avatar menu).
// Lists the signed-in user's recent processing jobs (uploads, AI runs, exports)
// from GET /api/jobs, so they can check whether a past job finished or failed
// after the live upload dock has gone away. Each row expands to its archived log.
import { useState } from 'react'
import { useJobsList } from '../hooks/useJobs'
import { useJob } from '../hooks/useJob'
import type { JobSummary, JobStatusValue } from '../types/job'

const STATUS_META: Record<JobStatusValue, { icon: string; label: string; color: string }> = {
  queued:                { icon: '⧗', label: 'Queued',     color: '#9ca3af' },
  processing:            { icon: '⟳', label: 'Processing',  color: '#3b82f6' },
  completed:             { icon: '✓', label: 'Completed',   color: '#10b981' },
  completed_with_errors: { icon: '⚠', label: 'With errors', color: '#f59e0b' },
  failed:                { icon: '✕', label: 'Failed',      color: '#ef4444' },
}

const KIND_ICON: Record<string, string> = {
  upload: '⬆️', ai_pipeline: '🤖', ai_embed: '🧠', ai_reprocess: '♻️',
  active_learning: '🎯', export: '📦', maintenance: '🛠️', model_train: '🧬', model_upload: '📟',
}

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24); if (d < 7) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

function StatusPill({ status }: { status: JobStatusValue }) {
  const meta = STATUS_META[status] ?? STATUS_META.queued
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, flexShrink: 0,
      fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: 999,
      color: meta.color, border: `1px solid ${meta.color}55`, backgroundColor: `${meta.color}14`,
    }}>
      {meta.icon} {meta.label}
    </span>
  )
}

function JobRow({ job }: { job: JobSummary }) {
  const [open, setOpen] = useState(false)
  // Only fetch the full event log when the row is expanded.
  const { data: detail, isLoading } = useJob(open ? job.job_id : null)

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', marginBottom: '0.5rem', overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.75rem', width: '100%', textAlign: 'left',
          padding: '0.625rem 0.875rem', background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-color)',
        }}
      >
        <span style={{ fontSize: '1.1rem' }}>{KIND_ICON[job.kind ?? ''] ?? '•'}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '0.8125rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {job.label || job.kind || 'Job'}
          </div>
          <div style={{ fontSize: '0.7rem', opacity: 0.55 }}>
            {timeAgo(job.created_at)}{job.event_count ? ` · ${job.event_count} log lines` : ''}
          </div>
        </div>
        <StatusPill status={job.status} />
        <span style={{ opacity: 0.4, fontSize: '0.75rem' }}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '0.625rem 0.875rem', backgroundColor: 'var(--surface)' }}>
          {job.error && (
            <div style={{ color: '#ef4444', fontSize: '0.75rem', marginBottom: '0.5rem' }}>Error: {job.error}</div>
          )}
          {isLoading && <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>Loading log…</div>}
          {detail && detail.events.length === 0 && !job.error && (
            <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>No log lines recorded for this job.</div>
          )}
          {detail && detail.events.length > 0 && (
            <div style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.7rem',
              maxHeight: 280, overflowY: 'auto', lineHeight: 1.6,
            }}>
              {detail.events.map((ev, i) => (
                <div key={ev.seq ?? i} style={{ display: 'flex', gap: 8, opacity: ev.type === 'file_failure' ? 1 : 0.85 }}>
                  <span style={{ opacity: 0.45, flexShrink: 0 }}>{ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''}</span>
                  <span style={{ color: ev.type === 'file_failure' ? '#ef4444' : 'inherit', wordBreak: 'break-word' }}>{ev.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function ProcessingHistoryPage() {
  const { data: jobs, isLoading, isError } = useJobsList()

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 0.25rem' }}>Processing history</h2>
      <p style={{ fontSize: '0.8125rem', opacity: 0.65, margin: '0 0 1.25rem' }}>
        Your recent uploads and AI runs. Expand a job to see its log and whether it finished or failed.
      </p>

      {isLoading && <div style={{ fontSize: '0.875rem', opacity: 0.6 }}>Loading…</div>}
      {isError && <div style={{ fontSize: '0.875rem', color: '#ef4444' }}>Couldn’t load your jobs. Please try again.</div>}
      {jobs && jobs.length === 0 && (
        <div style={{ fontSize: '0.875rem', opacity: 0.6, padding: '2rem 0', textAlign: 'center' }}>
          No processing jobs yet. Uploads and AI runs you start will appear here.
        </div>
      )}
      {jobs && jobs.map(job => <JobRow key={job.job_id} job={job} />)}
    </div>
  )
}
