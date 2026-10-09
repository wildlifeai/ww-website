// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The photo area of the viewer (MediaDetail): the image stage with its boxes, box drawing, pan
// and movement compare, the "No preview yet" state, and the filmstrip.
import { useState, useRef, useEffect } from 'react'
import type { ObservationRecord, MediaRecord } from './MediaBrowser'
import { groupByBox, isHumanReviewed, isAiLabel, observationLabel } from '../../lib/observations'
import { displayImageUrl } from '../../lib/mediaImageUrl'

type Box = { x: number; y: number; w: number; h: number }
type BoxFields = Pick<ObservationRecord, 'bbox_x' | 'bbox_y' | 'bbox_w' | 'bbox_h'>

// A label on a bounding box; the background carries the box's state.
const BOX_LABEL: React.CSSProperties = {
  border: 'none', cursor: 'pointer', fontFamily: 'inherit', lineHeight: 'inherit', color: '#fff',
  fontSize: '0.625rem', padding: '1px 4px', borderRadius: '2px', whiteSpace: 'nowrap',
}

const NAV_ARROW: React.CSSProperties = {
  position: 'absolute', top: '50%', transform: 'translateY(-50%)', zIndex: 6,
  width: 44, height: 44, borderRadius: '50%', border: 'none',
  backgroundColor: 'rgba(0,0,0,0.45)', color: '#fff', fontSize: '1.5rem',
  cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
}

// ── Movement overlay ─────────────────────────────────────────────────────────
// Pixel-difference vs the previous frame, tinted red, the fast way to spot the
// animal in a camera-trap burst. Best-effort: if the rendition host doesn't send
// CORS headers the canvas is tainted and we surface an "unavailable" note rather
// than crash. Downsamples to keep the per-pixel loop cheap on large originals.
function MovementOverlay({ currentUrl, prevUrl }: { currentUrl: string; prevUrl: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = (src: string) =>
      new Promise<HTMLImageElement>((resolve, reject) => {
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.onload = () => resolve(img)
        img.onerror = reject
        img.src = src
      })

    Promise.all([load(currentUrl), load(prevUrl)])
      .then(([cur, prev]) => {
        if (cancelled) return
        const scale = Math.min(1, 1280 / (cur.naturalWidth || 1280))
        const w = Math.max(1, Math.round((cur.naturalWidth || 1280) * scale))
        const h = Math.max(1, Math.round((cur.naturalHeight || 960) * scale))
        const canvas = canvasRef.current
        if (!canvas) return
        canvas.width = w
        canvas.height = h
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        if (!ctx) return
        try {
          ctx.drawImage(cur, 0, 0, w, h)
          const curData = ctx.getImageData(0, 0, w, h)
          ctx.drawImage(prev, 0, 0, w, h)
          const prevData = ctx.getImageData(0, 0, w, h)
          const out = ctx.createImageData(w, h)
          const TH = 38 // per-channel mean diff to count as "moved"
          for (let i = 0; i < curData.data.length; i += 4) {
            const d =
              (Math.abs(curData.data[i] - prevData.data[i]) +
                Math.abs(curData.data[i + 1] - prevData.data[i + 1]) +
                Math.abs(curData.data[i + 2] - prevData.data[i + 2])) / 3
            if (d > TH) {
              out.data[i] = 255
              out.data[i + 1] = 45
              out.data[i + 2] = 45
              out.data[i + 3] = 150
            } else {
              out.data[i + 3] = 0
            }
          }
          ctx.putImageData(out, 0, 0)
        } catch {
          setErr(true)
        }
      })
      .catch(() => {
        if (!cancelled) setErr(true)
      })

    return () => {
      cancelled = true
    }
  }, [currentUrl, prevUrl])

  if (err) {
    return (
      <div style={{
        position: 'absolute', top: 8, left: 8, zIndex: 6,
        backgroundColor: 'rgba(0,0,0,0.7)', color: '#fff',
        fontSize: '0.7rem', padding: '0.25rem 0.5rem', borderRadius: 'var(--radius)',
      }}>
        Movement compare unavailable for this image
      </div>
    )
  }
  return (
    <canvas
      ref={canvasRef}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
    />
  )
}

// ── Filmstrip carousel ───────────────────────────────────────────────────────
export function Filmstrip({ items, currentId, onSelect }: {
  items: MediaRecord[]
  currentId: string
  onSelect: (id: string) => void
}) {
  const stripRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)

  // Keep the current frame scrolled into view as the user steps through.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [currentId])

  if (items.length <= 1) return null
  return (
    <div
      ref={stripRef}
      style={{
        display: 'flex', gap: '0.375rem', overflowX: 'auto', padding: '0.5rem 0.75rem',
        backgroundColor: 'rgba(0,0,0,0.35)', borderTop: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      {items.map(m => {
        const url = displayImageUrl(m, 'thumb')
        const active = m.id === currentId
        const reviewed = m.observations.some(isHumanReviewed)
        const ai = m.observations.some(isAiLabel)
        const dot = reviewed ? '#3b82f6' : ai ? '#10b981' : '#9ca3af'
        return (
          <button
            key={m.id}
            ref={active ? activeRef : undefined}
            onClick={() => onSelect(m.id)}
            title={m.file_name || ''}
            style={{
              position: 'relative', flexShrink: 0, width: 84, height: 60,
              padding: 0, cursor: 'pointer', overflow: 'hidden',
              borderRadius: 4, background: '#000',
              border: active ? '2px solid var(--primary, #10b981)' : '2px solid transparent',
              opacity: active ? 1 : 0.65,
            }}
          >
            {url
              ? <img src={url} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : <span style={{ display: 'block', width: '100%', height: '100%', backgroundColor: 'rgba(255,255,255,0.08)' }} />}
            <span style={{
              position: 'absolute', bottom: 3, right: 3, width: 8, height: 8,
              borderRadius: '50%', backgroundColor: dot, boxShadow: '0 0 0 1px rgba(0,0,0,0.5)',
            }} />
          </button>
        )
      })}
    </div>
  )
}

// ── Bounding-box drawing ─────────────────────────────────────────────────────
// Mounted per observation being boxed (keyed by its id), so leaving draw mode or switching to
// another observation drops any half-drawn rectangle.
function BoxDrawer({ obsId, wrapRef, onDone, onSave }: {
  obsId: string
  wrapRef: React.RefObject<HTMLDivElement | null>
  onDone: () => void
  onSave: (obsId: string, fields: BoxFields) => void
}) {
  const [draft, setDraft] = useState<Box | null>(null)
  const drawStart = useRef<{ x: number; y: number } | null>(null)

  const normPos = (e: React.MouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 }
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    }
  }
  const onDrawDown = (e: React.MouseEvent) => { const p = normPos(e); drawStart.current = p; setDraft({ x: p.x, y: p.y, w: 0, h: 0 }) }
  const onDrawMove = (e: React.MouseEvent) => {
    const s = drawStart.current; if (!s) return
    const p = normPos(e)
    setDraft({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) })
  }
  const onDrawUp = () => {
    const d = draft; drawStart.current = null
    setDraft(null); onDone()
    if (d && d.w > 0.01 && d.h > 0.01) {
      onSave(obsId, { bbox_x: d.x, bbox_y: d.y, bbox_w: d.w, bbox_h: d.h })
    }
  }

  return (
    <>
      {/* Draft rectangle while drawing */}
      {draft && (
        <div style={{
          position: 'absolute', left: `${draft.x * 100}%`, top: `${draft.y * 100}%`,
          width: `${draft.w * 100}%`, height: `${draft.h * 100}%`,
          border: '2px dashed #f59e0b', borderRadius: '2px',
          backgroundColor: 'rgba(245,158,11,0.12)', pointerEvents: 'none',
        }} />
      )}

      {/* Drawing surface, active only in draw mode */}
      <div onMouseDown={onDrawDown} onMouseMove={onDrawMove} onMouseUp={onDrawUp}
        style={{ position: 'absolute', inset: 0, cursor: 'crosshair', zIndex: 5 }} />
    </>
  )
}

// ── Image stage ──────────────────────────────────────────────────────────────
export function MediaDetailImage({
  media, prevUrl, zoom, pan, onPan, brightness, contrast, movement, boxes, selectedObsId, onSelectObs,
  bboxObsId, onBoxDone, onSaveBox, onClose, onNext, onPrev, previewStuck, onRetryPreview,
}: {
  media: MediaRecord
  /** The previous photo's image, for the movement compare. */
  prevUrl: string | null
  zoom: number
  pan: { x: number; y: number }
  onPan: (pan: { x: number; y: number }) => void
  brightness: number
  contrast: number
  movement: boolean
  /** The observations whose boxes the annotation filter shows. */
  boxes: ObservationRecord[]
  selectedObsId: string | null
  onSelectObs: (id: string) => void
  /** The observation a box is being drawn for, if any. */
  bboxObsId: string | null
  onBoxDone: () => void
  onSaveBox: (obsId: string, fields: BoxFields) => void
  onClose: () => void
  onNext?: () => void
  onPrev?: () => void
  /** No preview by now and no job making one (the grid's "No thumbnail" rule): offer Retry. */
  previewStuck?: boolean
  /** Start the thumbnail backfill for this photo's deployment. */
  onRetryPreview?: () => void
}) {
  // A rendition URL that fails to load is treated like no preview, never a broken image.
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const resolvedUrl = displayImageUrl(media, 'full')
  const imgUrl = resolvedUrl && resolvedUrl !== failedUrl ? resolvedUrl : null
  const panStart = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  const [panning, setPanning] = useState(false)
  const imgWrapRef = useRef<HTMLDivElement>(null)

  const clearBox = (obsId: string) =>
    onSaveBox(obsId, { bbox_x: null, bbox_y: null, bbox_w: null, bbox_h: null })

  // ── Pan (drag when zoomed, not drawing) ────────────────────────────────────
  const onStageDown = (e: React.MouseEvent) => {
    if (bboxObsId || zoom === 1) return
    panStart.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
    setPanning(true)
  }
  const onStageMove = (e: React.MouseEvent) => {
    const s = panStart.current; if (!s) return
    onPan({ x: s.px + (e.clientX - s.x), y: s.py + (e.clientY - s.y) })
  }
  const onStageUp = () => { panStart.current = null; setPanning(false) }

  return (
    <div
      onClick={e => { if (e.target === e.currentTarget && !bboxObsId) onClose() }}
      onMouseDown={onStageDown}
      onMouseMove={onStageMove}
      onMouseUp={onStageUp}
      onMouseLeave={onStageUp}
      style={{
        flex: 1, minHeight: 0, position: 'relative',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden', padding: '1rem',
        cursor: zoom > 1 && !bboxObsId ? (panning ? 'grabbing' : 'grab') : 'default',
      }}
    >
      {onPrev && <button onClick={onPrev} title="Previous (←)" style={{ ...NAV_ARROW, left: 12 }}>‹</button>}
      {onNext && <button onClick={onNext} title="Next (→)" style={{ ...NAV_ARROW, right: 12 }}>›</button>}

      {imgUrl ? (
        <div
          ref={imgWrapRef}
          style={{
            position: 'relative', display: 'inline-block', lineHeight: 0,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transition: panning ? 'none' : 'transform 0.12s',
          }}
        >
          <img
            src={imgUrl}
            alt={media.file_name || ''}
            draggable={false}
            onError={() => setFailedUrl(imgUrl)}
            style={{
              maxWidth: '100%', maxHeight: '78vh', display: 'block',
              filter: `brightness(${brightness}%) contrast(${contrast}%)`,
            }}
          />

          {/* Movement compare overlay */}
          {movement && prevUrl && imgUrl && <MovementOverlay key={`${imgUrl}|${prevUrl}`} currentUrl={imgUrl} prevUrl={prevUrl} />}

          {/* Bounding boxes (respect the annotation filter). One box per position:
              a detection and its per-crop classifier row share a box (#162), so the
              box is drawn once with each row's label, and a label selects its row. */}
          {groupByBox(boxes).map(group => {
            const obs = group[0]
            const selectedInGroup = group.find(o => o.id === selectedObsId)
            const reviewed = group.some(isHumanReviewed)
            const color = selectedInGroup ? 'rgba(245,158,11,0.95)' : reviewed ? 'rgba(33,150,243,0.85)' : 'rgba(76,175,80,0.85)'
            return (
              // A mouse shortcut only: each label on the box is a button that selects its row.
              <div
                key={obs.id}
                role="presentation"
                onClick={() => { if (!selectedInGroup) onSelectObs(obs.id) }}
                style={{
                  position: 'absolute',
                  left: `${obs.bbox_x! * 100}%`, top: `${obs.bbox_y! * 100}%`,
                  width: `${obs.bbox_w! * 100}%`, height: `${obs.bbox_h! * 100}%`,
                  border: `2px solid ${color}`, borderRadius: '2px',
                  cursor: 'pointer', pointerEvents: bboxObsId ? 'none' : 'auto',
                }}
              >
                <span style={{
                  position: 'absolute', top: -18, left: 0, display: 'flex', gap: 2,
                }}>
                  {group.map(o => {
                    const conf = o.classification_probability
                    return (
                      <button
                        key={o.id}
                        type="button"
                        aria-pressed={o.id === selectedObsId}
                        onClick={e => { e.stopPropagation(); onSelectObs(o.id) }}
                        style={{ ...BOX_LABEL, backgroundColor: o.id === selectedObsId ? 'rgba(245,158,11,0.95)' : color }}
                      >
                        {observationLabel(o)} {conf ? `${(conf * 100).toFixed(0)}%` : ''}
                      </button>
                    )
                  })}
                </span>
                {!bboxObsId && selectedInGroup && (
                  <button
                    onClick={e => { e.stopPropagation(); clearBox(selectedInGroup.id) }}
                    title="Delete this box"
                    style={{
                      position: 'absolute', top: -8, right: -8, width: 18, height: 18, borderRadius: '50%',
                      border: 'none', cursor: 'pointer', backgroundColor: color, color: '#fff',
                      fontSize: '0.625rem', lineHeight: 1, padding: 0,
                    }}
                  >✕</button>
                )}
              </div>
            )
          })}

          {bboxObsId && <BoxDrawer key={bboxObsId} obsId={bboxObsId} wrapRef={imgWrapRef} onDone={onBoxDone} onSave={onSaveBox} />}
        </div>
      ) : (
        // Same rule as the grid card: Retry once nothing is making the preview (#300).
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.75rem 1rem', fontSize: '0.8125rem' }}>
          {previewStuck ? (
            <>
              <span>No preview yet</span>
              {onRetryPreview && (
                <button type="button" className="btn btn-outline" onClick={onRetryPreview} title="Make the missing previews for this deployment">
                  Retry
                </button>
              )}
            </>
          ) : (
            <span>Processing…</span>
          )}
        </div>
      )}

      {bboxObsId && (
        <div style={{
          position: 'absolute', bottom: 10, left: '50%', transform: 'translateX(-50%)', zIndex: 7,
          backgroundColor: 'rgba(245,158,11,0.92)', color: '#1f2937', fontWeight: 600,
          fontSize: '0.75rem', padding: '0.3rem 0.75rem', borderRadius: 'var(--radius)',
        }}>
          ▭ Drag on the image to draw a box · Esc to cancel
        </div>
      )}
    </div>
  )
}
