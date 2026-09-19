// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProjectDefaultsPanel — per-project capture + AI defaults (Settings).
// Sets projects.capture_method_id (default triggering method) and projects.model_id
// (default AI model). Both columns already exist on `projects`; writes are gated by RLS
// to project admins (a non-admin save surfaces an inline message).
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { supabase } from '../../config/supabase'

interface Project {
  id: string
  name: string
  capture_method_id: number | null
  model_id: string | null
}
interface CaptureMethod { id: number; value: string; description: string | null }
interface AiModel { id: string; name: string; version: string | null }

const CAPTURE_LABEL: Record<string, string> = {
  activityDetection: 'Activity detection (motion)',
  timeLapse: 'Timelapse',
}

export function ProjectDefaultsPanel({ projectId }: { projectId?: string } = {}) {
  const { user } = useAuth()
  const [projects, setProjects] = useState<Project[]>([])
  const [methods, setMethods] = useState<CaptureMethod[]>([])
  const [models, setModels] = useState<AiModel[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!user) return
    let cancelled = false
    setLoading(true)
    // Scope to one project when opened as a per-project action; otherwise list all.
    let projQuery = supabase.from('projects').select('id, name, capture_method_id, model_id').order('name')
    if (projectId) projQuery = projQuery.eq('id', projectId)
    Promise.all([
      projQuery,
      supabase.from('capture_methods').select('id, value, description').eq('is_active', true),
      // A converted or trained model is 'validated' (ready to load); 'deployed' means on a device.
      // Both are usable as a project's Species Brain.
      supabase.from('ai_models').select('id, name, version').in('status', ['validated', 'deployed']).order('name'),
    ]).then(([p, c, m]) => {
      if (cancelled) return
      setProjects((p.data as Project[] | null) ?? [])
      setMethods((c.data as CaptureMethod[] | null) ?? [])
      setModels((m.data as AiModel[] | null) ?? [])
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [user, projectId])

  const save = async (id: string, patch: Partial<Project>) => {
    setProjects(prev => prev.map(p => p.id === id ? { ...p, ...patch } : p))
    const { error } = await supabase.from('projects').update(patch).eq('id', id)
    setMsg(m => ({ ...m, [id]: error ? 'You need the Project Admin role to change this.' : 'Saved ✓' }))
    setTimeout(() => setMsg(m => ({ ...m, [id]: '' })), 2500)
  }

  if (loading) return <p style={{ opacity: 0.5 }}>Loading…</p>
  if (projects.length === 0) return <p style={{ fontSize: '0.85rem', opacity: 0.65 }}>No projects to configure yet.</p>

  const sel: React.CSSProperties = {
    padding: '0.3rem 0.45rem', fontSize: '0.8rem', border: '1px solid var(--border)',
    borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)', minWidth: 200,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      {projects.map(p => (
        <div key={p.id} style={{ border: projectId ? 'none' : '1px solid var(--border)', borderRadius: 'var(--radius)', padding: projectId ? 0 : '0.75rem 0.9rem' }}>
          {/* The slide-over header already names the project when scoped to one. */}
          {!projectId && <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '0.5rem' }}>{p.name}</div>}
          <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Default triggering method</span>
              <select
                value={p.capture_method_id ?? ''}
                onChange={e => save(p.id, { capture_method_id: e.target.value ? Number(e.target.value) : null })}
                style={sel}
              >
                <option value="">— Not set —</option>
                {methods.map(m => (
                  <option key={m.id} value={m.id}>{CAPTURE_LABEL[m.value] ?? m.description ?? m.value}</option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Default AI model</span>
              <select
                value={p.model_id ?? ''}
                onChange={e => save(p.id, { model_id: e.target.value || null })}
                style={sel}
              >
                <option value="">— Photos only (no AI) —</option>
                {models.map(m => (
                  <option key={m.id} value={m.id}>{m.name}{m.version ? ` ${m.version}` : ''}</option>
                ))}
              </select>
            </label>
          </div>
          {msg[p.id] && (
            <div style={{ fontSize: '0.72rem', marginTop: '0.4rem', color: msg[p.id].startsWith('Saved') ? 'var(--success)' : 'var(--error)' }}>
              {msg[p.id]}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
