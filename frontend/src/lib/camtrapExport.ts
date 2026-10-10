// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Download CamtrapDP (#328), shared by My Data, Reporting and Toolkit.
//
// The backend's export job (POST /api/exports/camtrapdp) packages the project's metadata from
// ww-backend's export-camtrap-dp with the original photos, and the job's result_url is a signed
// link to the ZIP. While the job is off on a backend (FEATURE_DISABLED, until its storage
// bucket exists), the download falls back to the Edge Function's metadata-only ZIP, as before.
//
// Each function that calls out takes the client, as lib/projectMembers.ts does.
import type { SupabaseClient } from '@supabase/supabase-js'
import type { JobInfo } from '../types/job'

export const SELECT_ONE_PROJECT = 'Select exactly one project from the project selector to download its CamtrapDP package.'
export const EXPORT_FAILED = 'The export failed. Try again later.'

type Post = (path: string, body: unknown) => Promise<unknown>

export type ExportStart = { kind: 'job'; jobId: string } | { kind: 'downloaded' }

export function camtrapFilename(projectId: string, now = new Date()): string {
  return `camtrapdp-${projectId}-${now.toISOString().slice(0, 10)}.zip`
}

export function saveFile(href: string, filename?: string) {
  const a = document.createElement('a')
  a.href = href
  if (filename) a.download = filename
  a.rel = 'noopener'
  a.click()
}

/** The function's own error text ({ error }) from a non-2xx answer, when it has one. */
async function functionErrorMessage(error: { message?: string; context?: unknown }): Promise<string> {
  const ctx = error.context as { json?: () => Promise<{ error?: string }> } | undefined
  try {
    const body = await ctx?.json?.()
    if (body?.error) return body.error
  } catch { /* not JSON */ }
  return error.message || EXPORT_FAILED
}

/** Start an export of the one selected project. Throws an Error with the message to show. */
export async function startCamtrapExport(
  projectIds: string[],
  post: Post,
  supabase: Pick<SupabaseClient, 'functions'>,
): Promise<ExportStart> {
  if (projectIds.length !== 1) throw new Error(SELECT_ONE_PROJECT)
  const projectId = projectIds[0]
  try {
    const res = await post('/api/exports/camtrapdp', { project_id: projectId }) as { data?: { job_id?: string } }
    const jobId = res?.data?.job_id
    if (!jobId) throw new Error(EXPORT_FAILED)
    return { kind: 'job', jobId }
  } catch (err) {
    if ((err as { code?: string })?.code !== 'FEATURE_DISABLED') throw err instanceof Error ? err : new Error(EXPORT_FAILED)
  }
  // Fallback: the metadata-only package, straight from the Edge Function.
  const { data, error } = await supabase.functions.invoke('export-camtrap-dp', { body: { project_id: projectId } })
  if (error) throw new Error(await functionErrorMessage(error))
  const blob = data instanceof Blob ? data : new Blob([data], { type: 'application/zip' })
  const url = URL.createObjectURL(blob)
  saveFile(url, camtrapFilename(projectId))
  URL.revokeObjectURL(url)
  return { kind: 'downloaded' }
}

export type ExportTone = 'progress' | 'done' | 'warning' | 'error'

/** One line for the job's state: what the button's status shows. */
export function exportStatus(job: Pick<JobInfo, 'status' | 'progress' | 'message' | 'error'>): { tone: ExportTone; text: string } {
  switch (job.status) {
    case 'completed':
      return { tone: 'done', text: job.message || 'Export ready.' }
    case 'completed_with_errors':
      return { tone: 'warning', text: job.message || 'Export ready, with some photos missing.' }
    case 'failed':
      return { tone: 'error', text: job.error || EXPORT_FAILED }
    default:
      return { tone: 'progress', text: `${job.message || 'Preparing the export…'} ${Math.round((job.progress || 0) * 100)}%` }
  }
}
