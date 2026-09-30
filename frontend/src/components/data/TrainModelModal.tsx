// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// TrainModelModal: Annotations → Actions → "Create species ID model…".
//
// Turns the current selection into an on-device species classifier (a Species
// Brain) the way the rat model was trained by hand in Edge Impulse: pick which
// species become classes, optionally fold everything else into a background
// class, name the model, and submit. The backend builds the dataset, drives
// Edge Impulse (or packages the dataset when no trainer is configured), compiles
// the int8 export with Vela and registers the model; this modal only collects the
// choices and then follows the job.
//
// Class order matters on the camera (index 1 is "the target" for a two-class
// model) and Edge Impulse orders classes alphabetically, so the default
// background label is chosen to sort first (see lib/trainingDataset.ts).

import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Modal } from '../ui/Modal'
import { apiClient } from '../../lib/apiClient'
import { supabase } from '../../config/supabase'
import { useJob } from '../../hooks/useJob'
import {
  backgroundCount, defaultBackgroundLabel, defaultLabelFor, sanitizeLabel, summarizeSelection,
  type ClassCandidate, type MediaLike,
} from '../../lib/trainingDataset'
import type { TrainModelRequest, TrainModelResponse, TrainingStatus } from '../../types/training'

interface Props {
  /** The selected media rows (with their observations). Fixed for the life of the modal. */
  media: MediaLike[]
  /** Projects the selection spans, offered as targets for "use as Species Brain" once trained. */
  projectIds: string[]
  onClose: () => void
}

interface ClassRow extends ClassCandidate {
  checked: boolean
  label: string
}

interface ManagedOrg { id: string; name: string }

const inputStyle: React.CSSProperties = {
  padding: '0.3rem 0.45rem', fontSize: '0.8rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)',
}
const labelStyle: React.CSSProperties = { fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.35rem' }
const hint: React.CSSProperties = { fontSize: '0.72rem', opacity: 0.65 }

function pct(n: number) { return `${Math.round(n * 100)}%` }

export function TrainModelModal({ media, projectIds, onClose }: Props) {
  const summary = useMemo(() => summarizeSelection(media), [media])

  const [status, setStatus] = useState<TrainingStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [orgs, setOrgs] = useState<ManagedOrg[]>([])
  const [orgId, setOrgId] = useState('')

  useEffect(() => {
    let cancelled = false
    apiClient.get('/api/models/train/status')
      .then(res => { if (!cancelled) setStatus((res as { data: TrainingStatus }).data) })
      .catch((e: Error) => { if (!cancelled) setStatusError(e.message) })
    apiClient.get('/api/models/managed-orgs')
      .then(res => {
        if (cancelled) return
        const list = ((res as { data?: ManagedOrg[] }).data ?? [])
        setOrgs(list)
        if (list.length === 1) setOrgId(list[0].id)
      })
      .catch(() => { /* the submit will explain */ })
    return () => { cancelled = true }
  }, [])

  // Class rows are initialised once from the selection; the user then edits them.
  const [rows, setRows] = useState<ClassRow[]>(() =>
    summary.classes.map((c, i) => ({ ...c, checked: i === 0, label: defaultLabelFor(c) })),
  )
  const checked = useMemo(() => rows.filter(r => r.checked), [rows])
  const targetNames = useMemo(() => new Set(checked.map(r => r.scientific_name.toLowerCase())), [checked])
  const others = backgroundCount(summary, targetNames)

  const [includeBackground, setIncludeBackground] = useState(true)
  const [backgroundOverride, setBackgroundOverride] = useState<string | null>(null)
  const autoBackground = useMemo(() => defaultBackgroundLabel(checked.map(r => sanitizeLabel(r.label))), [checked])
  const backgroundLabel = backgroundOverride ?? autoBackground

  const [modelName, setModelName] = useState('')
  const [description, setDescription] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [imageSize, setImageSize] = useState<96 | 160>(96)
  const [colour, setColour] = useState<'grayscale' | 'rgb'>('grayscale')
  const [epochs, setEpochs] = useState(30)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<TrainModelResponse | null>(null)
  const { data: job } = useJob(result?.job_id ?? null)

  const [brainMsg, setBrainMsg] = useState<string | null>(null)
  const [brainBusy, setBrainBusy] = useState(false)

  const setRow = (key: string, patch: Partial<ClassRow>) =>
    setRows(prev => prev.map(r => r.scientific_name.toLowerCase() === key ? { ...r, ...patch } : r))

  // ── Validation (mirrors backend/app/domain/training.py; the server re-checks) ──
  const minPer = status?.min_images_per_class ?? 20
  const recommended = status?.recommended_images_per_class ?? 100
  const maxClasses = status?.max_classes ?? 16
  const maxImages = status?.max_images ?? 3000
  const classImages = checked.reduce((n, r) => n + r.count, 0)
  const usedImages = classImages + (includeBackground ? others : 0)
  const classCount = checked.length + (includeBackground ? 1 : 0)

  const problems: string[] = []
  if (status && !status.enabled) problems.push('Model training is not enabled on this server (FF_MODEL_TRAINING_ENABLED).')
  if (checked.length === 0) problems.push('Tick at least one species to become a class.')
  if (classCount > maxClasses) problems.push(`The camera can run at most ${maxClasses} classes (background included).`)
  if (checked.length === 1 && !includeBackground) problems.push('A one-species model needs the background class, or it can never say "not that species".')
  for (const r of checked) {
    if (!sanitizeLabel(r.label)) problems.push(`Give "${r.scientific_name}" a label (letters, digits, spaces, - and _).`)
    if (r.count < minPer) problems.push(`"${r.scientific_name}" has ${r.count} labelled images; at least ${minPer} are needed.`)
  }
  if (includeBackground && others < minPer) problems.push(`The background class has ${others} images; at least ${minPer} are needed. Select more blanks or other species, or untick it.`)
  if (includeBackground && !sanitizeLabel(backgroundLabel)) problems.push('Give the background class a label.')
  const labels = checked.map(r => sanitizeLabel(r.label)).concat(includeBackground ? [sanitizeLabel(backgroundLabel)] : [])
  if (new Set(labels).size !== labels.length) problems.push('Two classes share the same label.')
  if (usedImages > maxImages) problems.push(`That is ${usedImages} images; one run takes at most ${maxImages}.`)
  if (modelName.trim().length < 2) problems.push('Name the model.')
  if (orgs.length > 1 && !orgId) problems.push('Choose which organisation owns the model.')

  const warnings: string[] = []
  for (const r of checked) {
    if (r.count >= minPer && r.count < recommended) warnings.push(`"${r.scientific_name}" has only ${r.count} images; ${recommended} or more per class gives a much better model.`)
  }
  if (includeBackground && others >= minPer && others < recommended) warnings.push(`The background class has only ${others} images; ${recommended} or more is recommended.`)
  const skipped = summary.unlabelled + summary.edgeOnly
  if (skipped > 0) warnings.push(`${skipped} selected image${skipped === 1 ? ' has' : 's have'} no usable label (unlabelled, or labelled only by a camera) and will be skipped.`)
  const humanShare = checked.reduce((n, r) => n + r.human, 0) / Math.max(1, classImages)
  if (checked.length > 0 && humanShare < 0.5) warnings.push(`Only ${pct(humanShare)} of the class images are human-reviewed; the rest rely on Cloud AI labels.`)

  const canSubmit = !!status && problems.length === 0 && !submitting
  const exportOnly = status?.mode === 'export_only'

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError(null)
    const body: TrainModelRequest = {
      media_ids: media.map(m => m.id),
      model_name: modelName.trim(),
      description: description.trim(),
      organisation_id: orgId,
      classes: checked.map(r => ({
        label: sanitizeLabel(r.label), scientific_name: r.scientific_name, taxon_id: r.taxon_id, vernacular_name: r.vernacular_name,
      })),
      include_background: includeBackground,
      background_label: includeBackground ? sanitizeLabel(backgroundLabel) : '',
      image_size: imageSize,
      colour,
      epochs,
      learning_rate: 0.001,
    }
    try {
      const res = await apiClient.post('/api/models/train', body) as { data: TrainModelResponse }
      setResult(res.data)
    } catch (e) {
      setSubmitError((e as Error).message || 'Could not start the training job')
    } finally {
      setSubmitting(false)
    }
  }

  const useAsBrain = async () => {
    if (!result?.model_id || projectIds.length === 0) return
    setBrainBusy(true)
    const { error } = await supabase.from('projects').update({ model_id: result.model_id }).in('id', projectIds)
    setBrainBusy(false)
    setBrainMsg(error
      ? 'You need the Project Admin role to change a project’s Species Brain. Ask an admin to pick it under Settings.'
      : `Saved: ${projectIds.length === 1 ? 'the project' : `${projectIds.length} projects`} will use this model. Prepare an SD card to load it on the cameras.`)
  }

  const done = job?.status === 'completed' || job?.status === 'completed_with_errors'
  const failed = job?.status === 'failed'

  // ── Progress view (after submit) ──────────────────────────────────────────
  if (result) {
    return (
      <Modal open={true} onClose={onClose} title={`🧬 ${modelName.trim()}`} persistent={!done && !failed}>
        <div style={{ fontSize: '0.8125rem', lineHeight: 1.6 }}>
          {!job && <p style={{ opacity: 0.7 }}>Queued…</p>}
          {job && (
            <>
              <div style={{ height: 8, background: 'var(--border)', borderRadius: 4, overflow: 'hidden', marginBottom: '0.5rem' }}>
                <div style={{ width: pct(job.progress ?? 0), height: '100%', background: failed ? 'var(--error)' : 'var(--primary)', transition: 'width 0.4s' }} />
              </div>
              <p style={{ margin: '0 0 0.75rem', whiteSpace: 'pre-wrap' }}>
                {failed ? `⚠ ${job.error || 'Training failed'}` : (job.message || (done ? 'Done' : 'Working…'))}
              </p>
            </>
          )}
          {!done && !failed && (
            <p style={hint}>
              {exportOnly
                ? 'Packaging the dataset takes a minute.'
                : 'Uploading, extracting features and training take several minutes. You can close this window; the job continues and appears under Processing history.'}
            </p>
          )}
          {done && job?.result_url && (
            <p><a href={job.result_url} className="btn" style={{ fontSize: '0.8rem' }} target="_blank" rel="noreferrer">📦 Download the dataset ZIP</a></p>
          )}
          {done && result.model_id && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
              <p style={{ margin: 0 }}>
                The model is registered and ready to load. To run it on the cameras: pick it as a project&apos;s Species Brain, then{' '}
                <Link to="/manifest">prepare an SD card</Link> (or sync from the mobile app).
              </p>
              {projectIds.length > 0 && !brainMsg && (
                <button className="btn" onClick={useAsBrain} disabled={brainBusy} style={{ alignSelf: 'flex-start', fontSize: '0.8rem' }}>
                  {brainBusy ? 'Saving…' : projectIds.length === 1 ? 'Use as this project’s Species Brain' : `Use as the Species Brain of these ${projectIds.length} projects`}
                </button>
              )}
              {brainMsg && <p style={{ margin: 0, color: brainMsg.startsWith('Saved') ? 'var(--success)' : 'var(--error)' }}>{brainMsg}</p>}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1rem' }}>
            <button className="btn btn-ghost" onClick={onClose}>{done || failed ? 'Close' : 'Run in background'}</button>
          </div>
        </div>
      </Modal>
    )
  }

  // ── Form view ─────────────────────────────────────────────────────────────
  return (
    <Modal open={true} onClose={onClose} title={`🧬 Create species ID model from ${media.length} images`} size="lg">
      <div style={{ fontSize: '0.8125rem', lineHeight: 1.6 }}>
        <p style={{ marginTop: 0, opacity: 0.8 }}>
          Train a Camera AI model (a Species Brain) from the selected, labelled images, following the recipe the rat
          model was trained with in Edge Impulse. The camera runs it on every capture to decide what it is looking at.
        </p>
        {statusError && <p style={{ color: 'var(--error)' }}>Could not reach the trainer: {statusError}</p>}
        {status && exportOnly && status.enabled && (
          <p style={{ ...hint, border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '0.4rem 0.6rem' }}>
            ⓘ Edge Impulse is not connected on this server. The dataset will be packaged (with a README of the recipe) for a
            manual run there; upload the int8 export on the Toolkit page afterwards.
          </p>
        )}

        {/* Classes */}
        <div style={labelStyle}>Classes</div>
        {summary.classes.length === 0 && (
          <p style={{ color: 'var(--error)' }}>None of the selected images has a species label. Label them first (Actions → Label as…).</p>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', marginBottom: '0.75rem', maxHeight: 260, overflowY: 'auto' }}>
          {rows.map(r => {
            const key = r.scientific_name.toLowerCase()
            const low = r.count < minPer
            return (
              <label key={key} style={{
                display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: '0.5rem', alignItems: 'center',
                padding: '0.4rem 0.6rem', borderRadius: 'var(--radius)',
                border: `1px solid ${r.checked ? 'var(--primary)' : 'var(--border)'}`,
                backgroundColor: r.checked ? 'rgba(76,175,80,0.06)' : 'transparent', cursor: 'pointer',
              }}>
                <input type="checkbox" checked={r.checked} onChange={() => setRow(key, { checked: !r.checked })} style={{ accentColor: 'var(--primary)' }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.vernacular_name ? `${r.vernacular_name} ` : ''}<span style={{ fontStyle: 'italic', opacity: 0.75, fontWeight: 400 }}>{r.scientific_name}</span>
                  </div>
                  <div style={{ ...hint, color: low && r.checked ? 'var(--error)' : undefined }}>
                    {r.count} image{r.count !== 1 ? 's' : ''} · {r.human} human-reviewed{low ? ` · needs ${minPer}` : ''}
                  </div>
                </div>
                {r.checked && (
                  <input
                    value={r.label}
                    onChange={e => setRow(key, { label: e.target.value })}
                    onClick={e => e.stopPropagation()}
                    title="Class label as the camera will report it"
                    style={{ ...inputStyle, width: 140 }}
                  />
                )}
              </label>
            )
          })}
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={includeBackground} onChange={e => setIncludeBackground(e.target.checked)} style={{ accentColor: 'var(--primary)' }} />
          <span>
            Add a <strong>background</strong> class from the other {others} image{others !== 1 ? 's' : ''}
            <span style={hint}> ({summary.blanks} blank, {others - summary.blanks} other species)</span>
          </span>
          {includeBackground && (
            <input
              value={backgroundLabel}
              onChange={e => setBackgroundOverride(e.target.value)}
              title="Background class label (sorted first so the camera treats your species as the target)"
              style={{ ...inputStyle, width: 140, marginLeft: 'auto' }}
            />
          )}
        </label>
        <p style={{ ...hint, margin: '0 0 0.75rem' }}>
          Class order on the camera is alphabetical, background first: the camera reports class 1 as the target of a two-class model.
        </p>

        {/* Model */}
        <div style={{ display: 'grid', gridTemplateColumns: orgs.length > 1 ? '1fr 1fr' : '1fr', gap: '0.75rem', marginBottom: '0.5rem' }}>
          <div>
            <div style={labelStyle}>Model name</div>
            <input value={modelName} onChange={e => setModelName(e.target.value)} placeholder="e.g. Rat brain" maxLength={60} style={{ ...inputStyle, width: '100%' }} autoFocus />
          </div>
          {orgs.length > 1 && (
            <div>
              <div style={labelStyle}>Owned by</div>
              <select value={orgId} onChange={e => setOrgId(e.target.value)} style={{ ...inputStyle, width: '100%' }}>
                <option value="">Choose an organisation</option>
                {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
          )}
        </div>
        <div style={{ marginBottom: '0.5rem' }}>
          <div style={labelStyle}>Description <span style={{ fontWeight: 400, opacity: 0.6 }}>(optional)</span></div>
          <input value={description} onChange={e => setDescription(e.target.value)} placeholder="Where the images came from, what it is for…" maxLength={500} style={{ ...inputStyle, width: '100%' }} />
        </div>

        <button onClick={() => setShowAdvanced(v => !v)} style={{ background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer', fontSize: '0.78rem', padding: 0, marginBottom: '0.5rem' }}>
          {showAdvanced ? '▾' : '▸'} Advanced (recipe)
        </button>
        {showAdvanced && (
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '0.75rem' }}>
            <label style={{ fontSize: '0.78rem' }}>Input size{' '}
              <select value={imageSize} onChange={e => setImageSize(Number(e.target.value) === 160 ? 160 : 96)} style={inputStyle}>
                <option value={96}>96 × 96 (recommended)</option>
                <option value={160}>160 × 160</option>
              </select>
            </label>
            <label style={{ fontSize: '0.78rem' }}>Colour{' '}
              <select value={colour} onChange={e => setColour(e.target.value === 'rgb' ? 'rgb' : 'grayscale')} style={inputStyle}>
                <option value="grayscale">Grayscale (rat model recipe)</option>
                <option value="rgb">RGB</option>
              </select>
            </label>
            <label style={{ fontSize: '0.78rem' }}>Epochs{' '}
              <input type="number" min={5} max={100} value={epochs} onChange={e => setEpochs(Math.min(100, Math.max(5, Number(e.target.value) || 30)))} style={{ ...inputStyle, width: 70 }} />
            </label>
            <span style={hint}>
              {status ? `${status.recipe.learning_block}, learning rate ${status.recipe.learning_rate}, augmentation on, ${pct(status.recipe.validation_split)} held out for testing.` : ''}
            </span>
          </div>
        )}

        {/* Checks */}
        {problems.length > 0 && (
          <ul style={{ margin: '0 0 0.5rem', paddingLeft: '1.1rem', color: 'var(--error)', fontSize: '0.78rem' }}>
            {problems.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        )}
        {warnings.length > 0 && (
          <ul style={{ margin: '0 0 0.5rem', paddingLeft: '1.1rem', color: '#b45309', fontSize: '0.78rem' }}>
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
        {submitError && <p style={{ color: 'var(--error)', fontSize: '0.78rem' }}>{submitError}</p>}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', marginTop: '0.75rem' }}>
          <span style={hint}>{usedImages} images across {classCount} class{classCount !== 1 ? 'es' : ''}</span>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
            <button className="btn" onClick={submit} disabled={!canSubmit}>
              {submitting ? 'Starting…' : exportOnly ? '📦 Prepare dataset' : '🧬 Train model'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}
