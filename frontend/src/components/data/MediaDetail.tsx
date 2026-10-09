import { useState, useEffect, useEffectEvent, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import type { ObservationRecord, MediaRecord } from './MediaBrowser'
import { confirmAllTargets, humanCreateFields, humanReviewFields, isHumanReviewed, isAiLabel, observationLabel } from '../../lib/observations'
import { SpeciesPicker } from './SpeciesPicker'
import { StatusBadge } from '../ui/StatusBadge'
import { AiOriginBadge } from '../ui/AiOriginBadge'
import { cameraModel, cameraScores } from '../../lib/cameraScores'
import { formatCaptureTime } from '../../lib/time'
import { canEditObservations, deleteObservation as deleteObservationRow } from '../../lib/observationWrites'
import { usePrefetchNeighbours } from '../../hooks/usePrefetchNeighbours'
import { Filmstrip, MediaDetailImage } from './MediaDetailImage'
import { displayImageUrl } from '../../lib/mediaImageUrl'

interface Props {
  media: MediaRecord
  /** Deployment IANA timezone for rendering the capture time in local time. */
  timezone?: string | null
  /** The filtered media set, in order — powers the filmstrip carousel + movement compare. */
  mediaList?: MediaRecord[]
  /** Jump to a specific media id (filmstrip click). */
  onSelect?: (id: string) => void
  onClose: () => void
  onUpdated: (updated: MediaRecord) => void
  /** Step to the next image (also used for Confirm/Blank auto-advance). */
  onNext?: () => void
  /** Step to the previous image. */
  onPrev?: () => void
  /** Observation to pre-select on open (e.g. the crop card the user clicked). */
  focusObsId?: string | null
  /** No preview by now and no job making one (the grid's "No thumbnail" rule): offer Retry. */
  previewStuck?: boolean
  /** Start the thumbnail backfill for this photo's deployment. */
  onRetryPreview?: () => void
}

type AnnotationFilter = 'all' | 'reviewed' | 'ai' | 'none'

const TOOL_BTN: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
  padding: '0.4rem 0.6rem', fontSize: '0.75rem', fontWeight: 600,
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  backgroundColor: 'var(--surface)', color: 'var(--text-color)',
  cursor: 'pointer', whiteSpace: 'nowrap',
}
const TOOL_BTN_ACTIVE: React.CSSProperties = { ...TOOL_BTN, backgroundColor: '#f59e0b', borderColor: '#f59e0b', color: '#fff' }
const CONFIRM_BTN: React.CSSProperties = { ...TOOL_BTN, color: '#10b981', borderColor: 'rgba(16,185,129,0.5)' }
const REJECT_BTN: React.CSSProperties = { ...TOOL_BTN, color: '#ef4444', borderColor: 'rgba(239,68,68,0.5)' }
// ── Read-only observation list (right panel) ─────────────────────────────────
function ObservationList({ media, selectedId, onSelectObs }: {
  media: MediaRecord
  selectedId: string | null
  onSelectObs: (id: string) => void
}) {
  if (media.observations.length === 0) {
    return (
      <p style={{ fontSize: '0.8125rem', opacity: 0.55, padding: '0.75rem 1rem' }}>
        No observations on this image yet. Use the actions under the image to add one.
      </p>
    )
  }
  const ranked = [...media.observations].sort((a, b) =>
    ((isHumanReviewed(b) ? 1 : 0) - (isHumanReviewed(a) ? 1 : 0)) ||
    ((b.classification_probability ?? 0) - (a.classification_probability ?? 0))
  )
  return (
    <div style={{ padding: '0.5rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {ranked.map(obs => {
        const reviewed = isHumanReviewed(obs)
        const status = reviewed ? 'reviewed' : isAiLabel(obs) ? 'ai' : 'issue'
        const selected = obs.id === selectedId
        return (
          <button
            key={obs.id}
            onClick={() => onSelectObs(obs.id)}
            style={{
              textAlign: 'left', width: '100%', cursor: 'pointer',
              padding: '0.625rem', borderRadius: 'var(--radius)', fontSize: '0.8125rem',
              backgroundColor: selected ? 'rgba(16,185,129,0.08)' : 'var(--surface)',
              border: selected ? '1px solid var(--primary, #10b981)' : '1px solid var(--border)',
              color: 'var(--text-color)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem', marginBottom: '0.35rem' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', flexWrap: 'wrap' }}>
                <StatusBadge
                  status={status}
                  size="sm"
                  label={status === 'ai' && obs.classification_probability != null
                    ? `AI ${(obs.classification_probability * 100).toFixed(0)}%`
                    : undefined}
                />
                <AiOriginBadge obs={obs} />
              </span>
              {obs.count != null && <span style={{ fontSize: '0.7rem', opacity: 0.6 }}>×{obs.count}</span>}
            </div>
            <div style={{ fontWeight: 600 }}>{observationLabel(obs)}</div>
            {(obs.scientific_name && obs.vernacular_name) && (
              <div style={{ fontStyle: 'italic', opacity: 0.6, fontSize: '0.75rem' }}>{obs.scientific_name}</div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.3rem', fontSize: '0.7rem', opacity: 0.6 }}>
              {obs.observation_type && <span>{obs.observation_type}</span>}
              {obs.life_stage && <span>· {obs.life_stage}</span>}
              {obs.sex && <span>· {obs.sex}</span>}
              {(obs.bbox_x != null) && <span>· ▭ box</span>}
            </div>
            <div style={{ fontSize: '0.6875rem', opacity: 0.45, marginTop: '0.3rem' }}>
              {reviewed ? '👤' : '🤖'} {obs.classified_by || '—'}
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ── Camera AI verdict: what the device decided in the field ──────────────────
// Every frame carries the on-device model's per-class scores in its EXIF
// (lib/cameraScores). A 📟 Camera AI observation is only reflected for a target
// score at or above the model's threshold, so this line is the only place a
// "no person 62%" frame shows the camera's decision, and it is there from the
// moment the row exists rather than after the cloud pipeline.
function CameraVerdict({ media }: { media: MediaRecord }) {
  const scores = cameraScores(media.exif_metadata)
  if (scores.length === 0) return null
  const model = cameraModel(media.exif_metadata)
  return (
    <div
      title="Per-class scores the camera wrote into this frame's EXIF UserComment"
      style={{
        margin: '0.25rem 1rem 0.5rem', padding: '0.5rem 0.625rem', fontSize: '0.75rem',
        borderRadius: 'var(--radius)', border: '1px solid rgba(139,92,246,0.45)', backgroundColor: 'rgba(139,92,246,0.08)',
      }}
    >
      <span style={{ fontWeight: 700, color: '#7c3aed' }}>📟 Camera AI</span>
      <span style={{ opacity: 0.6 }}> on the device{model ? `, ${model}` : ''}</span>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
        {scores.map(({ label, pct }, i) => (
          <span key={label} style={{ fontWeight: i === 0 ? 600 : 400, opacity: i === 0 ? 1 : 0.75 }}>
            {label} {pct}%
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Media-information (capture + EXIF), read-only ────────────────────────────
function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.75rem', borderBottom: '1px solid var(--border)', padding: '4px 0' }}>
      <span style={{ opacity: 0.6, whiteSpace: 'nowrap' }}>{label}</span>
      <span style={{ textAlign: 'right', wordBreak: 'break-word' }}>{value}</span>
    </div>
  )
}

function MediaInfoSection({ media, timezone }: { media: MediaRecord; timezone?: string | null }) {
  const aiCount = media.observations.filter(isAiLabel).length
  const exif = media.exif_metadata
  const entries = exif && typeof exif === 'object'
    ? Object.entries(exif).filter(([, v]) => v != null && typeof v !== 'object')
    : []
  return (
    <div style={{ padding: '0.75rem 1rem' }}>
      <strong style={{ fontSize: '0.8125rem' }}>Capture</strong>
      <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column' }}>
        {media.timestamp && <InfoRow label="Captured (local)" value={formatCaptureTime(media.timestamp, timezone)} />}
        {media.timestamp && <InfoRow label="Captured (UTC)" value={new Date(media.timestamp).toISOString().replace('T', ' ').replace('.000Z', ' UTC')} />}
        <InfoRow label="File" value={media.file_name || media.file_path.split('/').pop()} />
        <InfoRow label="Media type" value={media.file_mediatype} />
        <InfoRow label="Hosting" value={media.file_public ? 'Public URL' : 'Private (proxied)'} />
        <InfoRow label="AI detections" value={aiCount} />
        {media.media_comments && <InfoRow label="Comments" value={media.media_comments} />}
      </div>
      <div style={{ marginTop: '0.75rem' }}>
        <strong style={{ fontSize: '0.8125rem' }}>EXIF</strong>
        {entries.length === 0 ? (
          <p style={{ fontSize: '0.75rem', opacity: 0.5, margin: '0.5rem 0 0' }}>No EXIF metadata recorded for this image.</p>
        ) : (
          <div style={{ marginTop: '0.5rem', display: 'flex', flexDirection: 'column' }}>
            {entries.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.75rem', borderBottom: '1px solid var(--border)', padding: '3px 0' }}>
                <span style={{ opacity: 0.6, whiteSpace: 'nowrap' }}>{k}</span>
                <span style={{ textAlign: 'right', wordBreak: 'break-word' }}>{String(v)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Right panel: read-only observations + media info ────────────────────────
function DetailSidebar({ media, timezone, saveMsg, selectedObsId, onSelectObs, onClose }: {
  media: MediaRecord
  timezone?: string | null
  saveMsg: string | null
  selectedObsId: string | null
  onSelectObs: (id: string) => void
  onClose: () => void
}) {
  return (
    <div style={{ width: 360, flexShrink: 0, backgroundColor: 'var(--bg-color)', borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0.75rem 1rem', borderBottom: '1px solid var(--border)',
        position: 'sticky', top: 0, backgroundColor: 'var(--bg-color)', zIndex: 1,
      }}>
        <div style={{ fontWeight: 600, fontSize: '0.875rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {media.file_name || media.file_path.split('/').pop()}
        </div>
        <button onClick={onClose} title="Close (Esc)" style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1rem', opacity: 0.6 }}>✕</button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.6rem 1rem 0.3rem' }}>
        <strong style={{ fontSize: '0.8125rem' }}>Observations ({media.observations.length})</strong>
        {saveMsg && <span style={{ fontSize: '0.72rem', color: saveMsg.startsWith('Error') ? 'var(--error, #ef4444)' : 'var(--success, #10b981)' }}>{saveMsg}</span>}
      </div>
      <p style={{ fontSize: '0.7rem', opacity: 0.5, margin: '0 1rem 0.25rem' }}>
        Edit with the actions under the image. Select an observation to relabel, box, or remove it.
      </p>

      <CameraVerdict media={media} />

      <ObservationList media={media} selectedId={selectedObsId} onSelectObs={onSelectObs} />

      <details style={{ marginTop: 'auto', borderTop: '1px solid var(--border)' }}>
        <summary style={{ cursor: 'pointer', fontSize: '0.8125rem', fontWeight: 600, padding: '0.6rem 1rem', userSelect: 'none' }}>
          Media information
        </summary>
        <MediaInfoSection media={media} timezone={timezone} />
      </details>

      <div style={{ fontSize: '0.68rem', opacity: 0.45, padding: '0.6rem 1rem', borderTop: '1px solid var(--border)', lineHeight: 1.6 }}>
        <strong style={{ opacity: 0.7 }}>Shortcuts</strong><br />
        ←/→ prev/next · +/−/0 zoom · M movement · F filter · C confirm · A add · B blank · Del remove · Esc close
      </div>
    </div>
  )
}

// ── Main ─────────────────────────────────────────────────────────────────────
export function MediaDetail({ media, timezone, mediaList, onSelect, onClose, onUpdated, onNext, onPrev, focusObsId, previewStuck, onRetryPreview }: Props) {
  const { user } = useAuth()
  // Remove is offered only to users the database lets delete (#183).
  const { data: canRemove = false } = useQuery({
    queryKey: ['can-edit-observations', media.deployment_id, user?.id],
    queryFn: () => canEditObservations(supabase, user!.id, media.deployment_id),
    enabled: !!user,
    staleTime: 5 * 60_000,
  })

  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  // Image controls
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [brightness, setBrightness] = useState(100)
  const [contrast, setContrast] = useState(100)
  const [movement, setMovement] = useState(false)
  const [annFilter, setAnnFilter] = useState<AnnotationFilter>('all')

  // Selection + editing
  const [selectedObsId, setSelectedObsId] = useState<string | null>(null)
  const [picker, setPicker] = useState<null | 'add' | 'relabel'>(null)

  // Bounding-box drawing. Moving to another photo (or focused observation) leaves draw mode,
  // adjusted during render rather than in the reset effect below.
  const [bboxObsId, setBboxObsId] = useState<string | null>(null)
  const viewKey = `${media.id}|${focusObsId ?? ''}`
  const [bboxView, setBboxView] = useState(viewKey)
  if (bboxView !== viewKey) { setBboxView(viewKey); setBboxObsId(null) }

  // Reset per-image view state when the image changes. Pre-select the focused
  // observation (the crop card the user clicked) when it belongs to this image.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one reset per image, not a render loop
    setZoom(1); setPan({ x: 0, y: 0 }); setBrightness(100); setContrast(100)
    setMovement(false); setPicker(null)
    const focus = focusObsId && media.observations.some(o => o.id === focusObsId) ? focusObsId : null
    setSelectedObsId(focus)
  }, [media.id, focusObsId])

  const idx = useMemo(() => mediaList?.findIndex(m => m.id === media.id) ?? -1, [mediaList, media.id])
  usePrefetchNeighbours(mediaList, idx)
  const prevMedia = idx > 0 ? mediaList?.[idx - 1] : undefined
  const prevUrl = prevMedia ? displayImageUrl(prevMedia, 'full') : null

  const selectedObs = media.observations.find(o => o.id === selectedObsId) || null

  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }) }
  const zoomBy = (f: number) => setZoom(z => Math.min(6, Math.max(1, +(z * f).toFixed(2))))

  // ── Observation mutations (all stamp human provenance) ─────────────────────
  const updateObservation = async (obsId: string, fields: Partial<ObservationRecord>) => {
    setSaving(true); setSaveMsg(null)
    const patch = { ...fields, ...humanReviewFields({ userId: user?.id, userEmail: user?.email }) }
    const { error } = await supabase.from('observations').update(patch).eq('id', obsId)
    if (error) {
      setSaveMsg(`Error: ${error.message}`)
    } else {
      onUpdated({ ...media, observations: media.observations.map(o => o.id === obsId ? { ...o, ...patch } : o) })
      setSaveMsg('Saved ✓')
      setTimeout(() => setSaveMsg(null), 1800)
    }
    setSaving(false)
  }

  const addObservation = async (species?: { taxon_id?: string | null; scientific_name?: string | null; vernacular_name?: string | null }) => {
    setSaving(true); setSaveMsg(null)
    const newObs = {
      deployment_id: media.deployment_id,
      media_id: media.id,
      observation_level: 'media',
      observation_type: 'animal',
      taxon_id: species?.taxon_id ?? null,
      scientific_name: species?.scientific_name ?? null,
      vernacular_name: species?.vernacular_name ?? null,
      ...humanCreateFields({ userId: user?.id, userEmail: user?.email }),
    }
    const { data, error } = await supabase.from('observations').insert(newObs).select().single()
    if (error) {
      setSaveMsg(`Error: ${error.message}`)
    } else if (data) {
      const row = data as unknown as ObservationRecord
      onUpdated({ ...media, observations: [...media.observations, row] })
      setSelectedObsId(row.id)
      setSaveMsg('Added ✓')
      setTimeout(() => setSaveMsg(null), 1800)
    }
    setPicker(null)
    setSaving(false)
  }

  const deleteObservation = async (obsId: string) => {
    setSaving(true); setSaveMsg(null)
    try {
      await deleteObservationRow(supabase, obsId)
      onUpdated({ ...media, observations: media.observations.filter(o => o.id !== obsId) })
      if (selectedObsId === obsId) setSelectedObsId(null)
      setSaveMsg('Removed ✓')
      setTimeout(() => setSaveMsg(null), 1800)
    } catch (e) {
      setSaveMsg(`Error: ${(e as Error).message}`)
    }
    setSaving(false)
  }

  // Confirm: accept as-is (stamp human review). With no selection, confirm every
  // unreviewed per-model AI observation on the image (never the consensus row), then advance.
  const confirm = async () => {
    const targets = selectedObs
      ? [selectedObs]
      : confirmAllTargets(media.observations)
    if (targets.length === 0) { onNext?.(); return }
    // One batched update instead of N sequential requests (no per-row flicker).
    setSaving(true); setSaveMsg(null)
    const patch = humanReviewFields({ userId: user?.id, userEmail: user?.email })
    const ids = targets.map(t => t.id)
    const { error } = await supabase.from('observations').update(patch).in('id', ids)
    if (error) {
      setSaveMsg(`Error: ${error.message}`)
    } else {
      onUpdated({ ...media, observations: media.observations.map(o => ids.includes(o.id) ? { ...o, ...patch } : o) })
      setSaveMsg('Saved ✓')
      setTimeout(() => setSaveMsg(null), 1800)
      onNext?.()
    }
    setSaving(false)
  }

  const blank = async () => {
    const target = selectedObs ?? media.observations[0]
    if (!target) return
    await updateObservation(target.id, { observation_type: 'blank', scientific_name: null, vernacular_name: null, taxon_id: null })
    onNext?.()
  }

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────
  // An effect event always sees the current state, so the listener is added once.
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
    if (e.key === 'Escape') {
      if (picker) setPicker(null)
      else if (bboxObsId) setBboxObsId(null)
      else onClose()
      return
    }
    if (typing || picker) return
    switch (e.key) {
      case 'ArrowRight': if (!bboxObsId) onNext?.(); break
      case 'ArrowLeft': if (!bboxObsId) onPrev?.(); break
      case '+': case '=': zoomBy(1.25); break
      case '-': case '_': zoomBy(0.8); break
      case '0': resetView(); break
      case 'm': case 'M': if (prevUrl) setMovement(v => !v); break
      case 'f': case 'F': setAnnFilter(p => p === 'all' ? 'reviewed' : p === 'reviewed' ? 'ai' : p === 'ai' ? 'none' : 'all'); break
      case 'c': case 'C': if (!saving) confirm(); break
      case 'b': case 'B': if (!saving) blank(); break
      case 'a': case 'A': setPicker('add'); break
      case 'Delete': case 'Backspace': if (selectedObs && !saving && canRemove) deleteObservation(selectedObs.id); break
    }
  })
  useEffect(() => {
    const listener = (e: KeyboardEvent) => onKey(e)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  // Which boxes to render under the current filter.
  const visibleObs = media.observations.filter(o => {
    if (annFilter === 'none') return false
    if (annFilter === 'reviewed') return isHumanReviewed(o)
    if (annFilter === 'ai') return isAiLabel(o) && !isHumanReviewed(o)
    return true
  })

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 300, backgroundColor: 'rgba(0,0,0,0.86)', display: 'flex' }}>
      {/* ── LEFT: image stage + toolbar + filmstrip ─────────────── */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {/* Image stage */}
        <MediaDetailImage
          media={media} prevUrl={prevUrl} zoom={zoom} pan={pan} onPan={setPan}
          brightness={brightness} contrast={contrast} movement={movement}
          boxes={visibleObs} selectedObsId={selectedObsId} onSelectObs={setSelectedObsId}
          bboxObsId={bboxObsId} onBoxDone={() => setBboxObsId(null)} onSaveBox={updateObservation}
          onClose={onClose} onNext={onNext} onPrev={onPrev}
          previewStuck={previewStuck} onRetryPreview={onRetryPreview}
        />

        {/* Quick-action toolbar */}
        <div style={{
          display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.4rem',
          padding: '0.5rem 0.75rem', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)',
        }}>
          {/* View controls */}
          <button onClick={() => zoomBy(0.8)} style={TOOL_BTN} title="Zoom out (−)">🔍−</button>
          <span style={{ fontSize: '0.7rem', opacity: 0.6, minWidth: 38, textAlign: 'center' }}>{Math.round(zoom * 100)}%</span>
          <button onClick={() => zoomBy(1.25)} style={TOOL_BTN} title="Zoom in (+)">🔍+</button>
          <button onClick={resetView} style={TOOL_BTN} title="Reset view (0)">Reset</button>

          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem', opacity: 0.8 }} title="Brightness">
            🔆<input type="range" min={40} max={200} value={brightness} onChange={e => setBrightness(+e.target.value)} style={{ width: 70 }} />
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem', opacity: 0.8 }} title="Contrast">
            ◐<input type="range" min={40} max={200} value={contrast} onChange={e => setContrast(+e.target.value)} style={{ width: 70 }} />
          </label>

          <button
            onClick={() => prevUrl && setMovement(v => !v)}
            disabled={!prevUrl}
            style={movement ? TOOL_BTN_ACTIVE : { ...TOOL_BTN, opacity: prevUrl ? 1 : 0.4 }}
            title={prevUrl ? 'Highlight movement vs previous photo (M)' : 'No previous photo to compare'}
          >🌗 Movement</button>

          <select
            value={annFilter}
            onChange={e => setAnnFilter(e.target.value as AnnotationFilter)}
            title="Filter annotations shown (F)"
            style={{ ...TOOL_BTN, paddingRight: '0.4rem' }}
          >
            <option value="all">▣ All annotations</option>
            <option value="reviewed">Reviewed only</option>
            <option value="ai">AI (unreviewed) only</option>
            <option value="none">Hide annotations</option>
          </select>

          <span style={{ flex: 1 }} />

          {/* Editing verbs */}
          <button onClick={confirm} disabled={saving} style={CONFIRM_BTN} title={selectedObs ? 'Confirm selected (C)' : 'Confirm all AI labels (C)'}>
            ✓ {selectedObs ? 'Confirm' : 'Confirm all'}
          </button>
          <button onClick={() => setPicker(picker === 'add' ? null : 'add')} disabled={saving} style={picker === 'add' ? TOOL_BTN_ACTIVE : TOOL_BTN} title="Add observation (A)">
            ＋ Add
          </button>
          {selectedObs && (
            <>
              <button onClick={() => setPicker(picker === 'relabel' ? null : 'relabel')} disabled={saving} style={picker === 'relabel' ? TOOL_BTN_ACTIVE : TOOL_BTN} title="Relabel species">
                🏷 Relabel
              </button>
              <button onClick={() => setBboxObsId(bboxObsId === selectedObs.id ? null : selectedObs.id)} disabled={saving} style={bboxObsId === selectedObs.id ? TOOL_BTN_ACTIVE : TOOL_BTN} title="Draw bounding box">
                ▭ Box
              </button>
              <button onClick={blank} disabled={saving} style={REJECT_BTN} title="Mark false trigger / blank (B)">✕ Blank</button>
              {canRemove && (
                <button onClick={() => deleteObservation(selectedObs.id)} disabled={saving} style={REJECT_BTN} title="Remove observation (Del)">🗑 Remove</button>
              )}
            </>
          )}
        </div>

        {/* Inline species picker (add / relabel) */}
        {picker && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.75rem', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)' }}>
            <span style={{ fontSize: '0.75rem', fontWeight: 600, whiteSpace: 'nowrap' }}>
              {picker === 'add' ? 'Add observation:' : 'Relabel as:'}
            </span>
            <div style={{ flex: 1, maxWidth: 360 }}>
              <SpeciesPicker
                initialQuery={picker === 'relabel' ? (selectedObs?.vernacular_name || selectedObs?.scientific_name || '') : ''}
                placeholder="Search species…"
                disabled={saving}
                onSelect={sel => {
                  if (picker === 'add') {
                    addObservation({ taxon_id: sel.taxon_id, scientific_name: sel.scientific_name, vernacular_name: sel.vernacular_name })
                  } else if (selectedObs) {
                    updateObservation(selectedObs.id, {
                      taxon_id: sel.taxon_id, scientific_name: sel.scientific_name,
                      vernacular_name: sel.vernacular_name, observation_type: 'animal',
                    })
                    setPicker(null)
                  }
                }}
              />
            </div>
            {picker === 'add' && (
              <button onClick={() => addObservation()} disabled={saving} style={TOOL_BTN} title="Add without a species label">
                Add as unknown animal
              </button>
            )}
            <button onClick={() => setPicker(null)} style={TOOL_BTN}>Cancel</button>
          </div>
        )}

        {/* Filmstrip carousel */}
        {mediaList && onSelect && <Filmstrip items={mediaList} currentId={media.id} onSelect={onSelect} />}
      </div>

      {/* ── RIGHT: read-only observations + media info ──────────── */}
      <DetailSidebar
        media={media} timezone={timezone} saveMsg={saveMsg} onClose={onClose}
        selectedObsId={selectedObsId} onSelectObs={id => setSelectedObsId(prev => prev === id ? null : id)}
      />
    </div>
  )
}
