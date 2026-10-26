// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GbifDatasetField: the GBIF dataset a project publishes to (#373), in the Details tab, which only
// project admins see. Takes the dataset's key or its gbif.org link; blank clears it. The backend
// checks the key with GBIF before saving. Shown only while the GBF report is switched on.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import { apiClient } from '../../lib/apiClient'
import { fetchGbifDatasetKey, gbifDatasetUrl, saveGbifDataset } from '../../lib/gbifDataset'
import { useFeatureEnabled } from '../../hooks/useFeatureEnabled'
import type { SaveState } from '../../hooks/useSaveStatus'
import { SaveStatus } from './SaveStatus'

const INPUT: React.CSSProperties = {
  width: '100%', padding: '0.4rem 0.55rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem', boxSizing: 'border-box',
}
const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem', fontWeight: 500 }
const SECTION: React.CSSProperties = { borderTop: '1px solid var(--border)', marginTop: '1.5rem', paddingTop: '1rem' }

export function GbifDatasetField({ projectId }: { projectId: string }) {
  const on = useFeatureEnabled('gbfReport', projectId)
  const query = useQuery({
    queryKey: ['gbif-dataset-key', projectId],
    queryFn: () => fetchGbifDatasetKey(supabase, projectId),
    enabled: on,
    staleTime: 0,
  })
  if (!on || query.isPending) return null
  if (query.isError) return <p style={{ ...SECTION, fontSize: '0.8125rem', color: 'var(--error, #ef4444)' }}>{query.error.message}</p>
  return <DatasetForm projectId={projectId} stored={query.data} />
}

function DatasetForm({ projectId, stored }: { projectId: string; stored: string | null }) {
  const [current, setCurrent] = useState(stored)
  const [text, setText] = useState(stored ?? '')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<SaveState | null>(null)
  const dirty = text.trim() !== (current ?? '')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!dirty) return
    setSaving(true)
    try {
      const saved = await saveGbifDataset(apiClient.put, projectId, text)
      setCurrent(saved.gbif_dataset_key)
      setText(saved.gbif_dataset_key ?? '')
      setMsg({ ok: true, text: saved.dataset?.title ? `Saved: ${saved.dataset.title}` : 'Saved' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Not saved.' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} style={{ ...SECTION, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <label style={LABEL}>
        GBIF dataset
        <input
          type="text"
          value={text}
          placeholder="Dataset key or gbif.org link"
          onChange={e => { setText(e.target.value); setMsg(null) }}
          style={INPUT}
        />
      </label>
      {current && (
        <a href={gbifDatasetUrl(current)} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.8125rem', width: 'fit-content' }}>
          Open on GBIF
        </a>
      )}
      <SaveStatus status={msg} />
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button type="submit" className="btn" disabled={!dirty || saving} style={{ fontSize: '0.8125rem', padding: '0.4rem 0.9rem', opacity: !dirty || saving ? 0.6 : 1 }}>
          {saving ? 'Checking…' : 'Save'}
        </button>
      </div>
    </form>
  )
}
