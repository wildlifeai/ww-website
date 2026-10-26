// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// MotionLiveView: the live view page's body (#400). Connect, the picture with the HM0360's 16 x 16
// motion grid over it, the WAKE lamp, and the three motion settings, written to the camera and
// read back. How to read it and placement advice are in the motion detection guide.
import { useState, type CSSProperties } from 'react'
import { TriangleAlert } from 'lucide-react'
import { MD_COLS, MD_ROWS, OP_MD_INTERVAL, OP_RANGE, OP_SENSITIVITY, OP_THRESHOLD, type MotionOp } from '../../lib/liveView'
import { liveViewStatus, logText, type LiveViewFrame, type LiveViewSnapshot } from '../../lib/liveViewSession'
import { serialSupported, useLiveView } from '../../hooks/useLiveView'

const GRID_LINES = [
  ...Array.from({ length: MD_COLS - 1 }, (_, i) => `M${i + 1} 0V${MD_ROWS}`),
  ...Array.from({ length: MD_ROWS - 1 }, (_, i) => `M0 ${i + 1}H${MD_COLS}`),
].join('')

const SENSITIVITY = [{ value: 1, label: 'Low' }, { value: 2, label: 'Medium' }, { value: 3, label: 'High' }]

const INPUT: CSSProperties = {
  width: '7rem', padding: '0.375rem 0.5rem', fontSize: '0.875rem', borderRadius: 'var(--radius)',
  border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)',
}

type SetOp = (op: MotionOp, value: number) => void

export function MotionLiveView() {
  const { view, error, connect, stop, setOp } = useLiveView()

  if (!serialSupported()) {
    return (
      <p style={{ margin: 0, fontSize: '0.875rem' }}>
        This page needs Chrome or Edge on a computer. It talks to the camera&apos;s USB cable through
        the browser, which Firefox, Safari and phones cannot do.
      </p>
    )
  }

  const running = view !== null && view.phase !== 'stopped'
  const status = liveViewStatus(view)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {running
          ? <button type="button" className="btn" onClick={stop} disabled={view.phase === 'stopping'}>Stop</button>
          : <button type="button" className="btn" onClick={() => void connect()}>Connect to a camera</button>}
        {status && <span role="status" style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>{status}</span>}
      </div>
      {error && <p role="alert" style={{ margin: 0, fontSize: '0.8125rem', color: '#ef4444' }}>{error}</p>}
      {view && (
        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <Picture frame={view.frame} />
          <MotionPanel view={view} canSet={view.phase === 'starting' || view.phase === 'streaming'} onSetOp={setOp} />
        </div>
      )}
      {view && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Console log</summary>
          <pre style={{
            margin: '0.5rem 0 0', maxHeight: 260, overflow: 'auto', padding: '0.5rem 0.75rem', fontSize: '0.75rem',
            backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>{logText(view.log)}</pre>
        </details>
      )}
    </div>
  )
}

function Picture({ frame }: { frame: LiveViewFrame | null }) {
  const ratio = frame && frame.width > 0 && frame.height > 0 ? `${frame.width} / ${frame.height}` : '4 / 3'
  return (
    <div style={{ position: 'relative', flex: '1 1 320px', maxWidth: 640, aspectRatio: ratio, backgroundColor: '#000', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      {frame && (
        <img
          src={`data:image/jpeg;base64,${frame.image}`}
          alt="What the camera sees now"
          width={frame.width || 640}
          height={frame.height || 480}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
        />
      )}
      {frame?.cells && (
        <svg viewBox={`0 0 ${MD_COLS} ${MD_ROWS}`} preserveAspectRatio="none" aria-hidden="true" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          <path d={GRID_LINES} fill="none" stroke="rgba(0, 170, 255, 0.6)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {frame.cells.map(cell => (
            <rect
              key={cell}
              x={cell % MD_COLS}
              y={Math.floor(cell / MD_COLS)}
              width={1}
              height={1}
              fill="rgba(255, 40, 40, 0.2)"
              stroke="rgb(255, 40, 40)"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      )}
    </div>
  )
}

function MotionPanel({ view, canSet, onSetOp }: { view: LiveViewSnapshot; canSet: boolean; onSetOp: SetOp }) {
  const { frame, ops } = view
  const notes = [
    ops[10] === 0 ? 'The camera is switched off (op 10 is 0), so motion will not wake it.' : null,
    ops[OP_MD_INTERVAL] === 0 ? 'Motion detection is off (interval 0), so motion will not wake it.' : null,
    frame && !frame.cells ? 'These pictures carry no motion grid: the camera needs newer AI firmware.' : null,
  ].filter(n => n !== null)
  const wake = frame?.wake ?? null

  return (
    <div style={{ flex: '0 1 240px', display: 'flex', flexDirection: 'column', gap: '1rem', fontSize: '0.8125rem' }}>
      <div>
        <div
          role="img"
          aria-label={wake === null ? 'Wake: not known yet' : wake ? 'This picture would wake the camera' : 'This picture would not wake the camera'}
          style={{
            padding: '0.5rem', textAlign: 'center', fontWeight: 700, letterSpacing: '0.08em', borderRadius: 'var(--radius)',
            border: '1px solid var(--border)', backgroundColor: wake ? '#ef4444' : 'var(--surface)', color: wake ? '#fff' : 'var(--text-muted)',
          }}
        >
          WAKE
        </div>
        {frame?.cells && (
          <div style={{ marginTop: '0.375rem', color: 'var(--text-muted)' }}>
            {frame.cells.length} {frame.cells.length === 1 ? 'cell' : 'cells'} moving, wakes above {frame.threshold ?? 'unknown'}
          </div>
        )}
      </div>
      {notes.map(note => (
        <p key={note} style={{ margin: 0, display: 'flex', gap: '0.375rem', alignItems: 'flex-start' }}>
          <TriangleAlert size={14} color="#f59e0b" aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
          {note}
        </p>
      ))}
      <fieldset style={{ border: 'none', margin: 0, padding: 0 }}>
        <legend style={{ fontWeight: 500, marginBottom: '0.25rem', padding: 0 }}>Sensitivity</legend>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          {SENSITIVITY.map(s => (
            <label key={s.value} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
              <input
                type="radio"
                name="md-sensitivity"
                checked={ops[OP_SENSITIVITY] === s.value}
                disabled={!canSet || ops[OP_SENSITIVITY] === undefined}
                onChange={() => onSetOp(OP_SENSITIVITY, s.value)}
              />
              {s.label}
            </label>
          ))}
        </div>
      </fieldset>
      <NumberSetting label="Wake threshold (cells)" op={OP_THRESHOLD} value={ops[OP_THRESHOLD]} canSet={canSet} onSet={onSetOp} />
      <NumberSetting label="Motion interval (ms, 0 = off)" op={OP_MD_INTERVAL} value={ops[OP_MD_INTERVAL]} canSet={canSet} onSet={onSetOp} />
    </div>
  )
}

/** A number the camera holds: typed freely, sent on Enter or leaving the box, then shown as the
 *  camera reports it, so a value out of range or refused goes back to the camera's. */
function NumberSetting({ label, op, value, canSet, onSet }: {
  label: string
  op: MotionOp
  value: number | undefined
  canSet: boolean
  onSet: SetOp
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [min, max] = OP_RANGE[op]
  const commit = () => {
    if (draft === null) return
    setDraft(null)
    if (draft.trim() !== '' && Number(draft) !== value) onSet(op, Number(draft))
  }
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontWeight: 500 }}>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        step={1}
        value={draft ?? value ?? ''}
        disabled={!canSet || value === undefined}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === 'Enter') commit() }}
        style={INPUT}
      />
    </label>
  )
}
