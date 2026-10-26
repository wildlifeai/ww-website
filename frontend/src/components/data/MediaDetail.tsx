import { useState, useEffect, useEffectEvent, useMemo } from 'react'
import { Ban, Check, SlidersHorizontal, Square, Tag, Trash2, User, X, ZoomIn, ZoomOut } from 'lucide-react'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { useCanLabel } from '../../hooks/useCanLabel'
import type { ObservationRecord, MediaRecord } from './MediaBrowser'
import { isEdgeAi, isHumanReviewed, isHumanRow, isAiLabel, observationLabel, opinionSource, photoVerdict } from '../../lib/observations'
import {
  confirmLabel, labelOf, removeHumanRow, updateHumanRow, verdictIsReviewed, writeVerdict,
  type VerdictLabel, type VerdictWrite,
} from '../../lib/humanVerdict'
import { labelledSpecies } from '../../lib/speciesSuggestions'
import { showErrorToast, showUndoToast } from '../common/undoToastBus'
import { SpeciesPicker } from './SpeciesPicker'
import { StatusBadge } from '../ui/StatusBadge'
import { cameraModel, cameraScores } from '../../lib/cameraScores'
import { formatCaptureTime } from '../../lib/time'
import { usePrefetchNeighbours } from '../../hooks/usePrefetchNeighbours'
import { Filmstrip, MediaDetailImage } from './MediaDetailImage'
import { displayImageUrl } from '../../lib/mediaImageUrl'

interface Props {
  media: MediaRecord
  /** Deployment IANA timezone for rendering the capture time in local time. */
  timezone?: string | null
  /** The filtered media set, in order: powers the filmstrip carousel and movement compare. */
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
const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const VIEW_ROW: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem', fontSize: '0.75rem' }
const ROW_BTN: React.CSSProperties = {
  textAlign: 'left', width: '100%', cursor: 'pointer', padding: '0.5rem 0.625rem',
  borderRadius: 'var(--radius)', fontSize: '0.8125rem', color: 'var(--text-color)',
}

const pct = (p: number | null | undefined) => (p != null ? `${(p * 100).toFixed(0)}%` : '')
const hasBox = (o: ObservationRecord) => o.bbox_x != null
const labelText = (l: VerdictLabel) =>
  observationLabel({ vernacular_name: l.vernacular_name, scientific_name: l.scientific_name ?? null, observation_type: l.observation_type }) +
  (l.count ? ` ×${l.count}` : '')

// ── Verdict: what the photo's card says, and who said it ─────────────────────
function VerdictCard({ media, selectedId, onSelectObs }: {
  media: MediaRecord
  selectedId: string | null
  onSelectObs: (id: string) => void
}) {
  const { labelObs, isEmpty } = photoVerdict(media.observations)
  // A consensus "blank" has no label row; it is still the row that decided.
  const row = labelObs ?? (isEmpty ? media.observations.find(o => o.source_type === 'consensus') ?? null : null)
  if (!row && !isEmpty) {
    return <p style={{ ...MUTED, fontSize: '0.8125rem', margin: '0.25rem 1rem 0.75rem' }}>No label yet.</p>
  }
  const reviewed = !!labelObs && isHumanReviewed(labelObs)
  const selected = !!row && row.id === selectedId
  const body = (
    <>
      <StatusBadge status={reviewed ? 'reviewed' : 'ai'} size="sm" label={reviewed ? 'Reviewed' : `AI ${pct(row?.classification_probability)}`.trim()} />
      <div style={{ fontWeight: 700, fontSize: '0.9375rem', marginTop: '0.3rem' }}>
        {isEmpty ? 'Empty' : observationLabel(row!)}{!isEmpty && row?.count ? ` ×${row.count}` : ''}
      </div>
      {!isEmpty && row?.scientific_name && row.vernacular_name && (
        <div style={{ ...MUTED, fontStyle: 'italic', fontSize: '0.75rem' }}>{row.scientific_name}</div>
      )}
      {row && (
        <div style={{ ...MUTED, fontSize: '0.7rem', marginTop: '0.2rem' }}>
          {opinionSource(row)}{hasBox(row) ? ', boxed' : ''}
        </div>
      )}
    </>
  )
  return (
    <div style={{ padding: '0 0.75rem 0.5rem' }}>
      {row ? (
        <button
          type="button"
          onClick={() => onSelectObs(row.id)}
          aria-pressed={selected}
          style={{
            ...ROW_BTN,
            backgroundColor: selected ? 'rgba(16,185,129,0.08)' : 'var(--surface)',
            border: selected ? '1px solid var(--primary, #10b981)' : '1px solid var(--border)',
          }}
        >{body}</button>
      ) : <div style={{ ...ROW_BTN, cursor: 'default', border: '1px solid var(--border)' }}>{body}</div>}
    </div>
  )
}

// ── Model opinions: every other row, with the model that wrote it ────────────
// The camera's per-class EXIF scores are shown once: under its Camera AI row when one was
// reflected, otherwise as their own entry (a "no person 62%" frame has no row).
function ModelOpinions({ media, selectedId, onSelectObs }: {
  media: MediaRecord
  selectedId: string | null
  onSelectObs: (id: string) => void
}) {
  const { labelObs, isEmpty } = photoVerdict(media.observations)
  const verdictId = labelObs?.id ?? (isEmpty ? media.observations.find(o => o.source_type === 'consensus')?.id : undefined)
  const others = media.observations
    .filter(o => o.id !== verdictId)
    .sort((a, b) => (b.classification_probability ?? 0) - (a.classification_probability ?? 0))
  const scores = cameraScores(media.exif_metadata)
  const model = cameraModel(media.exif_metadata)
  const edgeRow = media.observations.find(isEdgeAi)
  const scoreLine = scores.length > 0 && (
    <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', marginTop: '0.2rem', fontSize: '0.72rem' }}>
      {scores.map(({ label, pct: p }, i) => (
        <span key={label} style={i === 0 ? { fontWeight: 600 } : MUTED}>{label} {p}%</span>
      ))}
    </div>
  )
  const count = others.length + (!edgeRow && scores.length > 0 ? 1 : 0)
  if (count === 0) return null
  return (
    <details style={{ padding: '0 0.75rem 0.5rem' }}>
      <summary style={{ cursor: 'pointer', fontSize: '0.8125rem', fontWeight: 600, padding: '0.25rem 0.25rem 0.4rem', userSelect: 'none' }}>
        {count} model opinion{count !== 1 ? 's' : ''}
      </summary>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
        {others.map(o => {
          const selected = o.id === selectedId
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onSelectObs(o.id)}
              aria-pressed={selected}
              style={{
                ...ROW_BTN,
                backgroundColor: selected ? 'rgba(16,185,129,0.08)' : 'var(--surface)',
                border: selected ? '1px solid var(--primary, #10b981)' : '1px solid var(--border)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
                <span style={{ fontWeight: 600 }}>{observationLabel(o)}{o.count ? ` ×${o.count}` : ''}</span>
                <span style={MUTED}>{isAiLabel(o) ? pct(o.classification_probability) : ''}</span>
              </div>
              <div style={{ ...MUTED, fontSize: '0.7rem', marginTop: '0.15rem' }}>
                {o === edgeRow && model && !o.source_model_version ? `Camera AI, ${model}` : opinionSource(o)}
                {hasBox(o) ? ', boxed' : ''}
              </div>
              {o === edgeRow && scoreLine}
            </button>
          )
        })}
        {!edgeRow && scoreLine && (
          <div
            title="Per-class scores the camera wrote into this frame's EXIF UserComment"
            style={{ ...ROW_BTN, cursor: 'default', border: '1px solid var(--border)', backgroundColor: 'var(--surface)' }}
          >
            <div style={{ fontWeight: 600 }}>Camera scores</div>
            <div style={{ ...MUTED, fontSize: '0.7rem', marginTop: '0.15rem' }}>{model ? `Camera AI, ${model}` : 'Camera AI'}</div>
            {scoreLine}
          </div>
        )}
      </div>
    </details>
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

// ── Right panel: verdict, model opinions, media info ─────────────────────────
function DetailSidebar({ media, timezone, canLabel, selectedObsId, onSelectObs, onClose }: {
  media: MediaRecord
  timezone?: string | null
  canLabel: boolean
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
        <button onClick={onClose} title="Close (Esc)" aria-label="Close" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', display: 'inline-flex', padding: 0 }}>
          <X size={18} aria-hidden />
        </button>
      </div>

      <strong style={{ fontSize: '0.8125rem', padding: '0.6rem 1rem 0.35rem' }}>Verdict</strong>
      <VerdictCard media={media} selectedId={selectedObsId} onSelectObs={onSelectObs} />
      <ModelOpinions media={media} selectedId={selectedObsId} onSelectObs={onSelectObs} />

      <details style={{ marginTop: 'auto', borderTop: '1px solid var(--border)' }}>
        <summary style={{ cursor: 'pointer', fontSize: '0.8125rem', fontWeight: 600, padding: '0.6rem 1rem', userSelect: 'none' }}>
          Media information
        </summary>
        <MediaInfoSection media={media} timezone={timezone} />
      </details>

      <div style={{ ...MUTED, fontSize: '0.68rem', padding: '0.6rem 1rem', borderTop: '1px solid var(--border)', lineHeight: 1.6 }}>
        <strong>Shortcuts</strong><br />
        {canLabel
          ? '←/→ prev/next, C confirm, A label, B blank, Del clear the box or your label, +/−/0 zoom, M movement, F boxes, Esc close'
          : '←/→ prev/next, +/−/0 zoom, M movement, F boxes, Esc close'}
      </div>
    </div>
  )
}

// ── View menu: image tools kept off the verb bar (03 #11) ────────────────────
function ViewMenu({ zoom, onZoom, onReset, brightness, onBrightness, contrast, onContrast, movement, canMove, onMovement, filter, onFilter, open, onOpen }: {
  zoom: number
  onZoom: (factor: number) => void
  onReset: () => void
  brightness: number
  onBrightness: (v: number) => void
  contrast: number
  onContrast: (v: number) => void
  movement: boolean
  canMove: boolean
  onMovement: () => void
  filter: AnnotationFilter
  onFilter: (f: AnnotationFilter) => void
  open: boolean
  onOpen: (open: boolean) => void
}) {
  const changed = zoom !== 1 || brightness !== 100 || contrast !== 100 || movement || filter !== 'all'
  return (
    <>
      <button
        type="button"
        onClick={() => onOpen(!open)}
        aria-expanded={open}
        style={changed ? { ...TOOL_BTN, borderColor: '#f59e0b' } : TOOL_BTN}
        title="Zoom, brightness, contrast, movement and which boxes show"
      >
        <SlidersHorizontal size={14} aria-hidden /> View{changed ? ` ${Math.round(zoom * 100)}%` : ''}
      </button>
      {open && (
        <>
          <div role="presentation" onClick={() => onOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 8 }} />
          <div
            className="card"
            style={{
              position: 'absolute', bottom: 'calc(100% + 6px)', left: '0.75rem', zIndex: 9, width: 260,
              padding: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.6rem',
              boxShadow: '0 6px 18px rgba(0,0,0,0.3)', color: 'var(--text-color)',
            }}
          >
            <div style={VIEW_ROW}>
              <span>Zoom</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                <button type="button" onClick={() => onZoom(0.8)} style={TOOL_BTN} title="Zoom out (−)" aria-label="Zoom out"><ZoomOut size={14} aria-hidden /></button>
                <span style={{ ...MUTED, minWidth: 38, textAlign: 'center' }}>{Math.round(zoom * 100)}%</span>
                <button type="button" onClick={() => onZoom(1.25)} style={TOOL_BTN} title="Zoom in (+)" aria-label="Zoom in"><ZoomIn size={14} aria-hidden /></button>
              </span>
            </div>
            <label style={VIEW_ROW}>
              Brightness
              <input type="range" min={40} max={200} value={brightness} onChange={e => onBrightness(+e.target.value)} style={{ width: 120 }} />
            </label>
            <label style={VIEW_ROW}>
              Contrast
              <input type="range" min={40} max={200} value={contrast} onChange={e => onContrast(+e.target.value)} style={{ width: 120 }} />
            </label>
            <label style={{ ...VIEW_ROW, justifyContent: 'flex-start', ...(canMove ? {} : MUTED) }} title={canMove ? undefined : 'No previous photo to compare'}>
              <input type="checkbox" checked={movement} disabled={!canMove} onChange={onMovement} />
              Highlight movement (M)
            </label>
            <label style={VIEW_ROW}>
              Boxes (F)
              <select value={filter} onChange={e => onFilter(e.target.value as AnnotationFilter)} style={{ ...TOOL_BTN, fontWeight: 400 }}>
                <option value="all">All</option>
                <option value="reviewed">Reviewed only</option>
                <option value="ai">AI only</option>
                <option value="none">Hidden</option>
              </select>
            </label>
            <button type="button" onClick={onReset} style={{ ...TOOL_BTN, justifyContent: 'center' }} title="Reset zoom and position (0)">Reset view</button>
          </div>
        </>
      )}
    </>
  )
}

// ── Main ─────────────────────────────────────────────────────────────────────
export function MediaDetail({ media, timezone, mediaList, onSelect, onClose, onUpdated, onNext, onPrev, focusObsId, previewStuck, onRetryPreview }: Props) {
  const { user } = useAuth()
  // Write controls only for project members and above on this photo's project (decision 1).
  const { canLabel, loading: roleLoading, projectId } = useCanLabel(media.deployment_id)

  const [saving, setSaving] = useState(false)

  // Image controls
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [brightness, setBrightness] = useState(100)
  const [contrast, setContrast] = useState(100)
  const [movement, setMovement] = useState(false)
  const [annFilter, setAnnFilter] = useState<AnnotationFilter>('all')
  const [viewOpen, setViewOpen] = useState(false)

  // Selection + editing
  const [selectedObsId, setSelectedObsId] = useState<string | null>(null)
  const [picker, setPicker] = useState(false)
  const [count, setCount] = useState('')

  // Bounding-box drawing.
  const [bboxObsId, setBboxObsId] = useState<string | null>(null)

  // Moving to another photo (or focused observation) resets the view, leaves draw mode and the
  // label bar, and pre-selects the focused observation (the crop card the user clicked) when it
  // belongs to this photo. Adjusted during render rather than in an effect.
  const viewKey = `${media.id}|${focusObsId ?? ''}`
  const [shownView, setShownView] = useState<string | null>(null)
  if (shownView !== viewKey) {
    setShownView(viewKey)
    setZoom(1); setPan({ x: 0, y: 0 }); setBrightness(100); setContrast(100)
    setMovement(false); setPicker(false); setBboxObsId(null)
    setSelectedObsId(focusObsId && media.observations.some(o => o.id === focusObsId) ? focusObsId : null)
  }

  const idx = useMemo(() => mediaList?.findIndex(m => m.id === media.id) ?? -1, [mediaList, media.id])
  usePrefetchNeighbours(mediaList, idx)
  const prevMedia = idx > 0 ? mediaList?.[idx - 1] : undefined
  const prevUrl = prevMedia ? displayImageUrl(prevMedia, 'full') : null

  // The picker offers the species of this photo's project before it searches, and the species
  // labelled on the photos in view until those are read.
  const seeds = useMemo(() => labelledSpecies((mediaList ?? [media]).flatMap(m => m.observations)), [mediaList, media])

  const selectedObs = media.observations.find(o => o.id === selectedObsId) || null
  // Only a person's own row can be boxed or removed; AI rows stay as the model wrote them.
  const editableObs = canLabel && selectedObs && isHumanRow(selectedObs) ? selectedObs : null

  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }) }
  const zoomBy = (f: number) => setZoom(z => Math.min(6, Math.max(1, +(z * f).toFixed(2))))

  // ── Label writes (lib/humanVerdict): a human row per photo, AI rows untouched ──
  // Each write shows an Undo toast, or the refusal as an error toast (never "saved").
  const actor = { userId: user?.id, userEmail: user?.email }
  const run = async (
    write: () => Promise<VerdictWrite<ObservationRecord>>,
    message: string,
    after: { advance?: boolean; select?: boolean } = {},
  ) => {
    if (saving || !canLabel) return
    setSaving(true)
    const base = media
    try {
      const result = await write()
      onUpdated({ ...base, observations: result.observations })
      if (after.select && result.rowId) setSelectedObsId(result.rowId)
      showUndoToast({
        message,
        onUndo: async () => { onUpdated({ ...base, observations: await result.undo() }) },
      })
      if (after.advance) onNext?.()
    } catch (e) {
      showErrorToast((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const applyLabel = (label: VerdictLabel) => {
    setPicker(false)
    return run(() => writeVerdict(supabase, media, label, actor), `Labelled ${labelText(label)}`, { select: true })
  }

  // Confirm agrees with the selected row, or with the photo's verdict, by writing it as the
  // person's label (one request), then moves on. A photo a person already ruled on just moves on.
  const confirm = () => {
    const label = selectedObs
      ? (isHumanRow(selectedObs) ? null : labelOf(selectedObs))
      : (verdictIsReviewed(media.observations) ? null : confirmLabel(media.observations))
    if (!label) { onNext?.(); return }
    return run(() => writeVerdict(supabase, media, label, actor), `Confirmed ${labelText(label)}`, { advance: true })
  }

  const blank = () => run(() => writeVerdict(supabase, media, { observation_type: 'blank' }, actor), 'Marked blank', { advance: true })

  const saveBox = (obsId: string, fields: Partial<ObservationRecord>) => {
    const drawing = fields.bbox_x != null
    return run(
      () => updateHumanRow(supabase, media, obsId, drawing ? { ...fields, observation_level: 'media' } : fields, actor),
      drawing ? 'Box saved' : 'Box removed',
    )
  }

  const removeLabel = (obsId: string) => {
    if (selectedObsId === obsId) setSelectedObsId(null)
    return run(() => removeHumanRow(supabase, media, obsId), 'Removed your label')
  }

  // Del clears the selected box first, then the label itself (03 #24).
  const clearSelected = () => {
    if (!editableObs) return
    if (hasBox(editableObs)) saveBox(editableObs.id, { bbox_x: null, bbox_y: null, bbox_w: null, bbox_h: null })
    else removeLabel(editableObs.id)
  }

  const openPicker = () => { setCount(''); setPicker(p => !p) }
  const countValue = /^\d+$/.test(count) && +count >= 1 ? +count : null

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────
  // An effect event always sees the current state, so the listener is added once.
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement
    if (e.key === 'Escape') {
      if (picker) setPicker(false)
      else if (viewOpen) setViewOpen(false)
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
      case 'c': case 'C': if (canLabel && !saving) confirm(); break
      case 'b': case 'B': if (canLabel && !saving) blank(); break
      // preventDefault: the key would otherwise type itself into the picker it opens.
      case 'a': case 'A': if (canLabel) { e.preventDefault(); openPicker() } break
      case 'Delete': case 'Backspace': if (!saving) clearSelected(); break
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

  const confirmTitle = selectedObs
    ? `Confirm ${observationLabel(selectedObs)} as your label (C)`
    : 'Confirm the verdict as your label and go to the next photo (C)'

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 300, backgroundColor: 'rgba(0,0,0,0.86)', display: 'flex' }}>
      {/* ── LEFT: image stage + toolbar + filmstrip ─────────────── */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {/* Image stage */}
        <MediaDetailImage
          media={media} prevUrl={prevUrl} zoom={zoom} pan={pan} onPan={setPan}
          brightness={brightness} contrast={contrast} movement={movement}
          boxes={visibleObs} selectedObsId={selectedObsId} onSelectObs={setSelectedObsId}
          editableObsId={editableObs?.id ?? null}
          bboxObsId={bboxObsId} onBoxDone={() => setBboxObsId(null)} onSaveBox={saveBox}
          onClose={onClose} onNext={onNext} onPrev={onPrev}
          previewStuck={previewStuck} onRetryPreview={onRetryPreview}
        />

        {/* Toolbar: the View menu, then the verbs */}
        <div style={{
          position: 'relative', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.4rem',
          padding: '0.5rem 0.75rem', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)',
        }}>
          <ViewMenu
            zoom={zoom} onZoom={zoomBy} onReset={resetView}
            brightness={brightness} onBrightness={setBrightness} contrast={contrast} onContrast={setContrast}
            movement={movement} canMove={!!prevUrl} onMovement={() => prevUrl && setMovement(v => !v)}
            filter={annFilter} onFilter={setAnnFilter} open={viewOpen} onOpen={setViewOpen}
          />

          <span style={{ flex: 1 }} />

          {canLabel && (
            <>
              <button onClick={confirm} disabled={saving} style={CONFIRM_BTN} title={confirmTitle}>
                <Check size={14} aria-hidden /> Confirm
              </button>
              <button onClick={openPicker} disabled={saving} style={picker ? TOOL_BTN_ACTIVE : TOOL_BTN} title="Label this photo (A)">
                <Tag size={14} aria-hidden /> Label
              </button>
              <button onClick={blank} disabled={saving} style={REJECT_BTN} title="Mark blank, nothing in the photo (B)">
                <Ban size={14} aria-hidden /> Blank
              </button>
              {editableObs && (
                <>
                  <button onClick={() => setBboxObsId(bboxObsId === editableObs.id ? null : editableObs.id)} disabled={saving} style={bboxObsId === editableObs.id ? TOOL_BTN_ACTIVE : TOOL_BTN} title="Draw a box around the animal">
                    <Square size={14} aria-hidden /> Box
                  </button>
                  <button onClick={() => removeLabel(editableObs.id)} disabled={saving} style={REJECT_BTN} title="Remove your label; the AI verdict shows again">
                    <Trash2 size={14} aria-hidden /> Remove
                  </button>
                </>
              )}
            </>
          )}
          {!canLabel && !roleLoading && <span style={{ ...MUTED, fontSize: '0.75rem' }}>View only</span>}
        </div>

        {/* Label bar: species, or a person, with an optional count */}
        {picker && canLabel && (
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.75rem', backgroundColor: 'var(--surface)', borderTop: '1px solid var(--border)' }}>
            <span style={{ fontSize: '0.75rem', fontWeight: 600, whiteSpace: 'nowrap' }}>Label as</span>
            <div style={{ flex: 1, minWidth: 200, maxWidth: 360 }}>
              <SpeciesPicker
                autoFocus
                seeds={seeds}
                projectIds={projectId ? [projectId] : undefined}
                placeholder="Search species…"
                disabled={saving}
                onSelect={sel => applyLabel({ observation_type: 'animal', ...sel, count: countValue })}
              />
            </div>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.75rem' }}>
              Count
              <input
                type="number" min={1} step={1} value={count} placeholder="1"
                onChange={e => setCount(e.target.value)}
                style={{ ...TOOL_BTN, width: 56, fontWeight: 400, cursor: 'text' }}
              />
            </label>
            <button onClick={() => applyLabel({ observation_type: 'human', count: countValue })} disabled={saving} style={TOOL_BTN} title="A person is in the photo">
              <User size={14} aria-hidden /> Person
            </button>
            <button onClick={() => applyLabel({ observation_type: 'animal', count: countValue })} disabled={saving} style={TOOL_BTN} title="An animal you cannot name">
              Unknown animal
            </button>
            <button onClick={() => setPicker(false)} style={TOOL_BTN}>Cancel</button>
          </div>
        )}

        {/* Filmstrip carousel */}
        {mediaList && onSelect && <Filmstrip items={mediaList} currentId={media.id} onSelect={onSelect} />}
      </div>

      {/* ── RIGHT: verdict, model opinions, media info ──────────── */}
      <DetailSidebar
        media={media} timezone={timezone} canLabel={canLabel} onClose={onClose}
        selectedObsId={selectedObsId} onSelectObs={id => setSelectedObsId(prev => prev === id ? null : id)}
      />
    </div>
  )
}
