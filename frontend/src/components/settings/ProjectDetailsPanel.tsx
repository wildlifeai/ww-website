// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProjectDetailsPanel: a project's name, description and website (Settings, #288). Saves through
// lib/projectDetails as the signed-in user, then shows the row the database returned.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import {
  buildDetailsPatch, fetchProjectDetails, formFromDetails, saveProjectDetails, validateDetails,
  type DetailsForm, type ProjectDetails,
} from '../../lib/projectDetails'

const INPUT: React.CSSProperties = {
  width: '100%', padding: '0.4rem 0.55rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem', boxSizing: 'border-box',
}
const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem', fontWeight: 500 }
const ERROR: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 400, color: 'var(--error, #ef4444)' }

export function ProjectDetailsPanel({ projectId, onSaved }: {
  projectId: string
  /** Called with the stored row after a save, so the caller can show the new name. */
  onSaved: (saved: ProjectDetails) => void
}) {
  const query = useQuery({
    queryKey: ['project-details', projectId],
    queryFn: () => fetchProjectDetails(supabase, projectId),
    staleTime: 0,
    gcTime: 0,
  })

  if (query.isPending) return <p style={{ opacity: 0.5 }}>Loading…</p>
  if (query.isError) return <p style={ERROR}>{query.error.message}</p>
  if (!query.data) return <p style={{ opacity: 0.7 }}>This project is not available to you.</p>
  return <DetailsFields stored={query.data} onSaved={onSaved} />
}

function DetailsFields({ stored, onSaved }: { stored: ProjectDetails; onSaved: (saved: ProjectDetails) => void }) {
  const [current, setCurrent] = useState(stored)
  const [form, setForm] = useState<DetailsForm>(() => formFromDetails(stored))
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const initial = formFromDetails(current)
  const dirty = (Object.keys(form) as (keyof DetailsForm)[]).some(k => form[k] !== initial[k])
  const invalid = validateDetails(form)

  const set = (k: keyof DetailsForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const value = e.target.value
    setForm(f => ({ ...f, [k]: value }))
    setMsg(null)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (invalid || !dirty) return
    setSaving(true)
    try {
      const saved = await saveProjectDetails(supabase, current.id, buildDetailsPatch(form))
      setCurrent(saved)
      setForm(formFromDetails(saved))
      setMsg({ ok: true, text: 'Saved ✓' })
      onSaved(saved)
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Not saved.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      <label style={LABEL}>
        Name
        <input type="text" value={form.name} onChange={set('name')} aria-invalid={!!invalid} style={INPUT} />
        {invalid && <span style={ERROR}>{invalid}</span>}
      </label>
      <label style={LABEL}>
        Description
        <textarea value={form.description} onChange={set('description')} rows={3} style={{ ...INPUT, resize: 'vertical' }} />
      </label>
      <label style={LABEL}>
        Website
        <input type="text" inputMode="url" value={form.website} onChange={set('website')} style={INPUT} />
      </label>
      {msg && <div style={msg.ok ? { fontSize: '0.8125rem', color: 'var(--success, #10b981)' } : ERROR}>{msg.text}</div>}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button type="submit" className="btn" disabled={!!invalid || !dirty || saving} style={{ fontSize: '0.8125rem', padding: '0.4rem 0.9rem', opacity: invalid || !dirty || saving ? 0.6 : 1 }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  )
}
