// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProcessingHistoryPage, /processing (opened from the avatar menu and the upload dock).
// Lists the signed-in user's recent processing jobs (uploads, AI runs, exports)
// from GET /api/jobs, so they can check whether a past job finished or failed
// after the live upload dock has gone away. Each row expands to its archived log;
// `?job=<id>` opens that row (the dock links a failed upload here).
//
// The status filter runs in the read, and "Load more" reads the next page (UI audit 01 E1).
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Bot, Brain, Check, ChevronDown, ChevronRight, Circle, Clock, Cpu, Dna, Image, LoaderCircle,
  Package, RefreshCw, Target, TriangleAlert, Upload, Wrench, X, type LucideIcon,
} from 'lucide-react'
import { useJobHistory } from '../hooks/useJobs'
import { useJob } from '../hooks/useJob'
import { JOB_STATUS_FILTERS } from '../lib/jobsList'
import type { JobSummary, JobStatusValue } from '../types/job'

const STATUS_META: Record<JobStatusValue, { icon: LucideIcon; label: string; color: string }> = {
  queued:                { icon: Clock,         label: 'Queued',      color: '#9ca3af' },
  processing:            { icon: LoaderCircle,  label: 'Processing',  color: '#3b82f6' },
  completed:             { icon: Check,         label: 'Completed',   color: '#10b981' },
  completed_with_errors: { icon: TriangleAlert, label: 'With errors', color: '#f59e0b' },
  failed:                { icon: X,             label: 'Failed',      color: '#ef4444' },
}

const KIND_ICON: Record<string, LucideIcon> = {
  upload: Upload, ai_pipeline: Bot, ai_embed: Brain, ai_reprocess: RefreshCw,
  active_learning: Target, export: Package, maintenance: Wrench, model_train: Dna, model_upload: Cpu,
  media_prep: Image,
}

const CONTROL_STYLE: React.CSSProperties = {
  padding: '0.35rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8rem',
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
  const Icon = meta.icon
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, flexShrink: 0,
      fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: 999,
      color: meta.color, border: `1px solid ${meta.color}55`, backgroundColor: `${meta.color}14`,
    }}>
      <Icon size={12} aria-hidden="true" /> {meta.label}
    </span>
  )
}

function JobRow({ job, defaultOpen }: { job: JobSummary; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const rowRef = useRef<HTMLDivElement>(null)
  // Only fetch the full event log when the row is expanded.
  const { data: detail, isLoading } = useJob(open ? job.job_id : null)
  const KindIcon = KIND_ICON[job.kind ?? ''] ?? Circle

  // A row opened from a link scrolls into view.
  useEffect(() => {
    if (defaultOpen) rowRef.current?.scrollIntoView({ block: 'nearest' })
  }, [defaultOpen])

  return (
    <div ref={rowRef} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', marginBottom: '0.5rem', overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.75rem', width: '100%', textAlign: 'left',
          padding: '0.625rem 0.875rem', background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-color)',
        }}
      >
        <KindIcon size={18} aria-hidden="true" style={{ flexShrink: 0, color: 'var(--text-muted)' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '0.8125rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {job.label || job.kind || 'Job'}
          </div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {timeAgo(job.created_at)}{job.event_count ? ` · ${job.event_count} log lines` : ''}
          </div>
        </div>
        <StatusPill status={job.status} />
        {open
          ? <ChevronDown size={14} aria-hidden="true" style={{ color: 'var(--text-muted)' }} />
          : <ChevronRight size={14} aria-hidden="true" style={{ color: 'var(--text-muted)' }} />}
      </button>

      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '0.625rem 0.875rem', backgroundColor: 'var(--surface)' }}>
          {job.error && (
            <div style={{ color: '#ef4444', fontSize: '0.75rem', marginBottom: '0.5rem' }}>Error: {job.error}</div>
          )}
          {detail?.result_url && (detail.status === 'completed' || detail.status === 'completed_with_errors') && (
            <div style={{ fontSize: '0.75rem', marginBottom: '0.5rem' }}>
              {detail.message && <span style={{ color: 'var(--text-muted)' }}>{detail.message} </span>}
              <a href={detail.result_url} style={{ color: 'var(--primary)' }}>Download</a>
            </div>
          )}
          {isLoading && <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Loading log…</div>}
          {detail && detail.events.length === 0 && !job.error && !detail.result_url && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>No log lines recorded for this job.</div>
          )}
          {detail && detail.events.length > 0 && (
            <div style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.7rem',
              maxHeight: 280, overflowY: 'auto', lineHeight: 1.6,
            }}>
              {detail.events.map(ev => (
                <div key={ev.seq} style={{ display: 'flex', gap: 8 }}>
                  <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''}</span>
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
  const [status, setStatus] = useState('')
  const filter = JOB_STATUS_FILTERS.find(f => f.value === status) ?? JOB_STATUS_FILTERS[0]
  const { data, isLoading, isError, isPlaceholderData, hasNextPage, fetchNextPage, isFetchingNextPage } = useJobHistory(filter.statuses)
  const jobs = data?.pages.flatMap(p => p.jobs)
  const [params] = useSearchParams()
  const openJob = params.get('job')

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 0.25rem' }}>Processing history</h2>
      <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', margin: '0 0 1rem' }}>
        Your uploads and AI runs, newest first. Open one to see its log.
      </p>

      {isLoading && <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>Loading…</div>}
      {isError && <div style={{ fontSize: '0.875rem', color: '#ef4444' }}>Couldn’t load your jobs. Please try again.</div>}
      {jobs && jobs.length === 0 && !status && (
        <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)', padding: '2rem 0', textAlign: 'center' }}>
          No processing jobs yet. Uploads and AI runs you start will appear here.
        </div>
      )}
      {jobs && (jobs.length > 0 || status !== '') && (
        <>
          <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Job status" style={{ ...CONTROL_STYLE, marginBottom: '0.75rem' }}>
            {JOB_STATUS_FILTERS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          {jobs.length === 0 && (
            <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)', padding: '1rem 0' }}>No jobs with this status.</div>
          )}
          {jobs.map(job => <JobRow key={job.job_id} job={job} defaultOpen={job.job_id === openJob} />)}
          {hasNextPage && (
            <button
              type="button"
              disabled={isFetchingNextPage || isPlaceholderData}
              onClick={() => fetchNextPage()}
              style={{ ...CONTROL_STYLE, display: 'block', margin: '0.5rem auto 0', cursor: 'pointer' }}
            >
              {isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </div>
  )
}
