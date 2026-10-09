// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// EditLocationDialog: correct one deployment's location from Insights > Deployments (#288).
// The form starts from the row as the database holds it, saves through lib/deploymentLocation
// (the write runs as the user, and a refusal comes back as an error), then reads the row again
// and shows what was stored, including the time zone recomputed from the coordinates.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from '../ui/Modal'
import { supabase } from '../../config/supabase'
import {
  buildLocationPayload, coordinatesMoved, fetchDeploymentLocation, formFromLocation, saveDeploymentLocation,
  validateLocation, type DeploymentLocation, type LocationForm,
} from '../../lib/deploymentLocation'

interface Props {
  deploymentId: string
  /** `changed` is true when a save went through, so the caller can refetch its list. */
  onClose: (changed: boolean) => void
}

const INPUT: React.CSSProperties = {
  width: '100%', padding: '0.4rem 0.55rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem', boxSizing: 'border-box',
}
const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem', fontWeight: 500 }
const ROW2: React.CSSProperties = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }
const NOTE: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 400, opacity: 0.7 }
const ERROR: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 400, color: 'var(--error, #ef4444)' }
const BTN: React.CSSProperties = {
  padding: '0.4rem 0.9rem', fontSize: '0.8125rem', borderRadius: 'var(--radius)', cursor: 'pointer',
  border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-color)',
}

export function EditLocationDialog({ deploymentId, onClose }: Props) {
  const [saved, setSaved] = useState(false)
  const query = useQuery({
    queryKey: ['deployment-location', deploymentId],
    queryFn: () => fetchDeploymentLocation(supabase, deploymentId),
    staleTime: 0,
    gcTime: 0,
  })
  const close = () => onClose(saved)

  let body: React.ReactNode
  if (query.isPending) body = <p style={{ opacity: 0.5 }}>Loading…</p>
  else if (query.isError) body = <p style={ERROR}>{query.error.message}</p>
  else if (!query.data) body = <p style={{ opacity: 0.7 }}>This deployment is not available to you.</p>
  else {
    body = (
      // A fresh read after a save remounts the form, so it shows what the database holds.
      <LocationFields
        key={query.dataUpdatedAt}
        stored={query.data}
        saved={saved}
        onSaved={async () => { setSaved(true); await query.refetch() }}
        onCancel={close}
      />
    )
  }

  return <Modal open onClose={close} title="Edit location" size="sm">{body}</Modal>
}

function LocationFields({ stored, saved, onSaved, onCancel }: {
  stored: DeploymentLocation
  saved: boolean
  onSaved: () => Promise<void>
  onCancel: () => void
}) {
  const [form, setForm] = useState<LocationForm>(() => formFromLocation(stored))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const initial = formFromLocation(stored)
  const dirty = (Object.keys(form) as (keyof LocationForm)[]).some(k => form[k] !== initial[k])
  const errors = validateLocation(form)
  const valid = Object.keys(errors).length === 0
  const accuracyCleared = coordinatesMoved(form, stored) && stored.accuracy != null && form.accuracy === initial.accuracy

  const set = (k: keyof LocationForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const value = e.target.value
    setForm(f => ({ ...f, [k]: value }))
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!valid || !dirty) return
    setSaving(true)
    setError(null)
    try {
      await saveDeploymentLocation(stored.id, buildLocationPayload(form, stored))
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Not saved.')
    } finally {
      setSaving(false)
    }
  }

  const field = (k: keyof LocationForm, label: string, extra?: React.ReactNode) => (
    <label style={LABEL}>
      {label}
      <input type="text" inputMode="decimal" value={form[k]} onChange={set(k)} aria-invalid={!!errors[k]} style={INPUT} />
      {errors[k] && <span style={ERROR}>{errors[k]}</span>}
      {extra}
    </label>
  )

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      <label style={LABEL}>
        Location name
        <input type="text" value={form.name} onChange={set('name')} aria-invalid={!!errors.name} style={INPUT} />
        {errors.name && <span style={ERROR}>{errors.name}</span>}
      </label>
      <label style={LABEL}>
        Description
        <textarea value={form.description} onChange={set('description')} rows={2} style={{ ...INPUT, resize: 'vertical' }} />
      </label>
      <div style={ROW2}>
        {field('latitude', 'Latitude')}
        {field('longitude', 'Longitude')}
      </div>
      <div style={ROW2}>
        {field('altitude', 'Altitude (m)')}
        {field('accuracy', 'Accuracy (m)', accuracyCleared && <span style={NOTE}>Cleared on save: it was for the old point.</span>)}
      </div>
      <div style={NOTE}>Time zone: {stored.timezone ?? 'none'}. It follows the coordinates when you save.</div>

      {error && <div style={ERROR}>{error}</div>}
      {saved && !dirty && !error && <div style={{ fontSize: '0.8125rem', color: 'var(--success, #10b981)' }}>Saved ✓</div>}

      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <button type="button" onClick={onCancel} style={BTN}>{saved && !dirty ? 'Close' : 'Cancel'}</button>
        <button type="submit" className="btn" disabled={!valid || !dirty || saving} style={{ fontSize: '0.8125rem', padding: '0.4rem 0.9rem', opacity: !valid || !dirty || saving ? 0.6 : 1 }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  )
}
