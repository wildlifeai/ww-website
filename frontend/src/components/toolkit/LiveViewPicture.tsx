// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// LiveViewPicture: the live view's left column (#400). The latest frame with the HM0360's motion
// grid over it, in 16 or 15 rows, and under it the WAKE lamp and the motion status lines, as
// live_view.py shows them. With motion detection off it is the plain picture.
import { TriangleAlert } from 'lucide-react'
import { MD_COLS, OP_MD_INTERVAL, gridCells, type GridRows } from '../../lib/liveView'
import type { LiveViewFrame, LiveViewSnapshot } from '../../lib/liveViewSession'
import { MONO } from './liveViewStyles'

function gridLines(rows: GridRows): string {
  return [
    ...Array.from({ length: MD_COLS - 1 }, (_, i) => `M${i + 1} 0V${rows}`),
    ...Array.from({ length: rows - 1 }, (_, i) => `M0 ${i + 1}H${MD_COLS}`),
  ].join('')
}

export function LiveViewPicture({ view, rows }: { view: LiveViewSnapshot; rows: GridRows }) {
  return (
    <div style={{ flex: '1 1 360px', maxWidth: 640, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <Picture frame={view.frame} rows={rows} showGrid={view.motionDetection} />
      {view.motionDetection ? <MotionStatus view={view} rows={rows} /> : (
        <p style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          Motion detection is off: plain live picture, a frame every {view.intervalMs} ms.
        </p>
      )}
    </div>
  )
}

function Picture({ frame, rows, showGrid }: { frame: LiveViewFrame | null; rows: GridRows; showGrid: boolean }) {
  const ratio = frame && frame.width > 0 && frame.height > 0 ? `${frame.width} / ${frame.height}` : '4 / 3'
  const grid = showGrid && frame?.cells ? gridCells(frame.cells, rows) : null
  return (
    <div style={{ position: 'relative', width: '100%', aspectRatio: ratio, backgroundColor: '#000', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
      {frame && (
        <img
          src={`data:image/jpeg;base64,${frame.image}`}
          alt="What the camera sees now"
          width={frame.width || 640}
          height={frame.height || 480}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
        />
      )}
      {grid && (
        <svg viewBox={`0 0 ${MD_COLS} ${rows}`} preserveAspectRatio="none" aria-hidden="true" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          <path d={gridLines(rows)} fill="none" stroke="rgba(0, 170, 255, 0.6)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {grid.cells.map(c => (
            <rect
              key={`${c.row}-${c.col}`}
              x={c.col}
              y={c.row}
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

function MotionStatus({ view, rows }: { view: LiveViewSnapshot; rows: GridRows }) {
  const { frame, ops, md } = view
  const wake = frame?.wake ?? null
  const outside = frame?.cells ? gridCells(frame.cells, rows).outside : 0
  const notes = [
    ops[10] === 0 ? 'The camera is switched off (op 10 is 0), so motion will not wake it.' : null,
    ops[OP_MD_INTERVAL] === 0 ? 'Motion detection is off on the camera (MD interval 0), so motion will not wake it.' : null,
    frame && !frame.cells ? 'These pictures carry no motion grid: the camera needs newer AI firmware.' : null,
  ].filter(n => n !== null)
  const lines = frame?.cells
    ? [
        ['motion', `${frame.cells.length} ${frame.cells.length === 1 ? 'cell' : 'cells'}${outside ? ` (+${outside} beyond row ${rows})` : ''}`],
        ['threshold', `${frame.threshold ?? '-'} (B ${frame.thresholdB ?? '-'})`],
        ['wake', wake === null ? '-' : wake ? 'YES' : 'no'],
        ['wakes', `${md.wakes} of ${md.frames} frames`],
      ]
    : [['motion', '-'], ['wakes', `${md.wakes} of ${md.frames} frames`]]

  return (
    <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start', flexWrap: 'wrap', fontSize: '0.8125rem' }}>
      <div
        role="img"
        aria-label={wake === null ? 'Wake: not known yet' : wake ? 'This picture would wake the camera' : 'This picture would not wake the camera'}
        style={{
          padding: '0.5rem 1rem', fontWeight: 700, letterSpacing: '0.08em', borderRadius: 'var(--radius)',
          border: '1px solid var(--border)', backgroundColor: wake ? '#ef4444' : 'var(--surface)', color: wake ? '#fff' : 'var(--text-muted)',
        }}
      >
        WAKE
      </div>
      <dl style={{ ...MONO, margin: 0, display: 'grid', gridTemplateColumns: 'auto auto', columnGap: '0.75rem', rowGap: '0.125rem' }}>
        {lines.map(([k, v]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt style={{ color: 'var(--text-muted)' }}>{k}</dt>
            <dd style={{ margin: 0 }}>{v}</dd>
          </div>
        ))}
      </dl>
      {notes.map(note => (
        <p key={note} style={{ margin: 0, flexBasis: '100%', display: 'flex', gap: '0.375rem', alignItems: 'flex-start' }}>
          <TriangleAlert size={14} color="#f59e0b" aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
          {note}
        </p>
      ))}
    </div>
  )
}
