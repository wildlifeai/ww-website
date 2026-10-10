// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ModelLabelMapper — post-upload step that captures what a model's output
// classes mean: which are target species (predicts: taxon, mapped via
// SpeciesPicker), which are a type such as a person (predicts: type, an
// observation_type) and which are background/negative classes. Saved to
// ai_models.label_map through PUT /api/models/{model_id}/label-map, which refuses a map
// that breaks LM-10 (backend/app/domain/label_map.py) and writes as the caller (RLS:
// organisation_manager). This lets the website reflect on-device predictions as real
// observations and skip negatives. Labels come from the model's own
// class order (detection_capabilities), so they stay aligned with labels.txt.
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { supabase } from '../../config/supabase'
import { saveLabelMap, type LabelProblems } from '../../lib/labelMap'
import { SpeciesPicker } from '../data/SpeciesPicker'

// The observation types a class may predict (LM-10): blank is what background means.
type TargetType = 'human' | 'vehicle' | 'animal'

interface LabelEntry {
  role: 'target' | 'background'
  predicts?: 'taxon' | 'type'
  observation_type?: TargetType
  taxon_id?: string | null
  scientific_name?: string | null
  vernacular_name?: string | null
  threshold?: number
}
type LabelMap = Record<string, LabelEntry>

// What a class asserts. A target with no `predicts` predates it and is a species.
type Kind = 'taxon' | 'type' | 'background'
const KINDS: { kind: Kind; label: string }[] = [
  { kind: 'taxon', label: 'Target species' },
  { kind: 'type', label: 'Target type' },
  { kind: 'background', label: 'Background / negative' },
]
const TYPES: { value: TargetType; label: string }[] = [
  { value: 'human', label: 'Person' },
  { value: 'vehicle', label: 'Vehicle' },
  { value: 'animal', label: 'Animal (any species)' },
]

function kindOf(entry: LabelEntry | undefined): Kind {
  if (!entry || entry.role === 'background') return 'background'
  return entry.predicts === 'type' ? 'type' : 'taxon'
}

// The entry for `prev` switched to `kind`; keeps a per-class threshold and, for a species, the taxon.
function withKind(prev: LabelEntry | undefined, kind: Kind): LabelEntry {
  const noTaxon = { taxon_id: null, scientific_name: null, vernacular_name: null }
  if (kind === 'background') return { role: 'background', ...noTaxon }
  const threshold = prev?.threshold !== undefined ? { threshold: prev.threshold } : {}
  if (kind === 'type') {
    return { role: 'target', predicts: 'type', observation_type: prev?.observation_type ?? 'human', ...noTaxon, ...threshold }
  }
  return {
    role: 'target',
    predicts: 'taxon',
    taxon_id: prev?.taxon_id ?? null,
    scientific_name: prev?.scientific_name ?? null,
    vernacular_name: prev?.vernacular_name ?? null,
    ...threshold,
  }
}

// Heuristic default: names like "not rat", "background", "blank" are negatives.
function looksNegative(label: string): boolean {
  return /^(not[ _-]|non[ _-]|no[ _-]|background|blank|none|unknown|negative|other|empty|absent)/i.test(label.trim())
}

export function ModelLabelMapper({ modelId, onDone }: { modelId: string; onDone?: () => void }) {
  const [modelName, setModelName] = useState('')
  const [labels, setLabels] = useState<string[]>([])
  const [map, setMap] = useState<LabelMap>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [problems, setProblems] = useState<LabelProblems>({})

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    supabase
      .from('ai_models')
      .select('name, detection_capabilities, label_map')
      .eq('id', modelId)
      .single()
      .then(({ data, error }) => {
        if (cancelled) return
        if (error || !data) { setLoading(false); return }
        const labs = ((data.detection_capabilities as string[] | null) || []).filter(Boolean)
        const saved = (data.label_map as LabelMap | null) || {}
        const init: LabelMap = {}
        for (const l of labs) {
          init[l] = saved[l] ?? { role: looksNegative(l) ? 'background' : 'target' }
        }
        setModelName((data.name as string) || '')
        setLabels(labs)
        setMap(init)
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [modelId])

  // Editing a label clears the problem the last save reported for it.
  const edit = (label: string, next: (prev: LabelEntry | undefined) => LabelEntry) => {
    setMap(m => ({ ...m, [label]: next(m[label]) }))
    setProblems(p => {
      const rest = { ...p }
      delete rest[label]
      return rest
    })
  }

  const setKind = (label: string, kind: Kind) => edit(label, prev => withKind(prev, kind))

  const setType = (label: string, observation_type: TargetType) =>
    edit(label, prev => ({ ...withKind(prev, 'type'), observation_type }))

  const setSpecies = (label: string, sel: { taxon_id: string | null; scientific_name: string; vernacular_name: string | null }) =>
    edit(label, prev => ({ ...withKind(prev, 'taxon'), taxon_id: sel.taxon_id, scientific_name: sel.scientific_name, vernacular_name: sel.vernacular_name }))

  const save = async () => {
    setSaving(true); setMsg(null); setProblems({})
    try {
      const refused = await saveLabelMap(modelId, map)
      if (refused) {
        setProblems(refused)
        setMsg('Error: not saved, fix the labels marked below')
        return
      }
      setMsg('Saved ✓')
      onDone?.()
    } catch (e) {
      setMsg(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p style={{ opacity: 0.5, fontSize: '0.85rem' }}>Loading model labels…</p>
  if (labels.length === 0) {
    return (
      <p style={{ fontSize: '0.85rem', opacity: 0.65 }}>
        This model reported no labels, so there's nothing to map. You can still use it.
      </p>
    )
  }

  const unmappedTargets = labels.filter(l => kindOf(map[l]) === 'taxon' && !map[l]?.scientific_name && !map[l]?.taxon_id)

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1rem', marginTop: '1rem', backgroundColor: 'var(--surface)' }}>
      <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>🏷️ What do this model's labels mean?</div>
      <p style={{ fontSize: '0.8rem', opacity: 0.7, margin: '0 0 0.875rem 0', lineHeight: 1.5 }}>
        Tell us which of <strong>{modelName || 'this model'}</strong>'s output classes are species
        to detect (mapped to a taxon), which are a type such as a person, and which are
        background/negative classes. This lets the site show the camera's on-device predictions
        as real observations and ignore the negatives.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {labels.map(label => {
          const entry = map[label]
          const kind = kindOf(entry)
          return (
            <div key={label} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', flexWrap: 'wrap', paddingBottom: '0.75rem', borderBottom: '1px solid var(--border)' }}>
              <code style={{ fontSize: '0.8rem', minWidth: 90, paddingTop: '0.35rem' }}>{label}</code>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                {KINDS.map(k => (
                  <label key={k.kind} style={{ fontSize: '0.78rem', display: 'inline-flex', alignItems: 'center', gap: '0.25rem', cursor: 'pointer', paddingTop: '0.35rem' }}>
                    <input type="radio" checked={kind === k.kind} onChange={() => setKind(label, k.kind)} />
                    {k.label}
                  </label>
                ))}
              </div>
              {kind === 'type' && (
                <select
                  aria-label={`Type for ${label}`}
                  value={entry?.observation_type ?? 'human'}
                  onChange={e => setType(label, e.target.value as TargetType)}
                  style={{ fontSize: '0.8rem' }}
                >
                  {TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              )}
              {kind === 'taxon' && (
                <div style={{ flex: 1, minWidth: 200 }}>
                  <SpeciesPicker
                    initialQuery={entry?.vernacular_name || entry?.scientific_name || label}
                    placeholder="Map to a species…"
                    onSelect={sel => setSpecies(label, sel)}
                  />
                  {entry?.scientific_name && (
                    <div style={{ fontSize: '0.72rem', opacity: 0.7, marginTop: '0.2rem' }}>
                      → <em>{entry.scientific_name}</em>{entry.vernacular_name ? ` (${entry.vernacular_name})` : ''}
                    </div>
                  )}
                </div>
              )}
              {problems[label] && (
                <div role="alert" style={{ flexBasis: '100%', fontSize: '0.75rem', color: 'var(--error, #ef4444)' }}>
                  {problems[label]}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.875rem' }}>
        <button className="btn" onClick={save} disabled={saving} style={{ fontSize: '0.85rem' }}>
          {saving ? 'Saving…' : 'Save label mapping'}
        </button>
        {unmappedTargets.length > 0 && (
          <span style={{ fontSize: '0.72rem', opacity: 0.6 }}>
            {unmappedTargets.length} target label{unmappedTargets.length > 1 ? 's' : ''} not yet mapped to a species
          </span>
        )}
        {msg && <span style={{ fontSize: '0.78rem', color: msg.startsWith('Error') ? 'var(--error, #ef4444)' : 'var(--success, #10b981)' }}>{msg}</span>}
      </div>
    </div>
  )
}
