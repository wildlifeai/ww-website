// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// LiveViewControls: the live view's Controls tab (#400), as live_view.py lays them out. Free run,
// Stop and One image; Save frame; the frame interval; the Motion detection switch with the three
// motion op params, written to the camera and read back, and the grid rows; Keep awake.
import { useState } from 'react'
import {
  MAX_FRAME_INTERVAL_MS, MIN_FRAME_INTERVAL_MS, OP_MD_INTERVAL, OP_RANGE, OP_SENSITIVITY, OP_THRESHOLD,
  type GridRows, type MotionOp,
} from '../../lib/liveView'
import type { LiveViewSnapshot, RunMode } from '../../lib/liveViewSession'
import { saveFrame } from './liveViewSave'
import { CHECK, INPUT, ROW, SECTION, SECTION_TITLE } from './liveViewStyles'

const SENSITIVITY = [{ value: 1, label: 'Low' }, { value: 2, label: 'Medium' }, { value: 3, label: 'High' }]
const RUN: { mode: RunMode; label: string }[] = [
  { mode: 'free', label: 'Free run' }, { mode: 'paused', label: 'Stop' }, { mode: 'one', label: 'One image' },
]

export interface ControlActions {
  freeRun: () => void
  pause: () => void
  oneImage: () => void
  setOp: (op: MotionOp, value: number) => void
  setFrameInterval: (ms: number) => void
  setMotionDetection: (on: boolean) => void
  setKeepAwake: (on: boolean) => void
}

export function LiveViewControls({ view, rows, onRows, actions }: {
  view: LiveViewSnapshot
  rows: GridRows
  onRows: (rows: GridRows) => void
  actions: ControlActions
}) {
  const [withGrid, setWithGrid] = useState(false)
  const live = view.phase !== 'stopping' && view.phase !== 'stopped'
  const run = { free: actions.freeRun, paused: actions.pause, one: actions.oneImage }
  const { ops } = view
  const intervalHint = view.intervalSet ? 'set here' : view.motionDetection && view.camera === 'HM0360' ? 'follows the MD interval' : 'default'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', fontSize: '0.8125rem' }}>
      <section style={SECTION}>
        <h3 style={SECTION_TITLE}>Run</h3>
        <div style={ROW}>
          {RUN.map(r => (
            <button
              key={r.mode}
              type="button"
              className={view.runMode === r.mode ? 'btn' : 'btn btn-outline'}
              aria-pressed={view.runMode === r.mode}
              disabled={!live}
              onClick={run[r.mode]}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div style={ROW}>
          <button type="button" className="btn btn-outline" disabled={!view.frame} onClick={() => view.frame && void saveFrame(view.frame, rows, withGrid)}>
            Save frame
          </button>
          <label style={CHECK}>
            <input type="checkbox" checked={withGrid} onChange={e => setWithGrid(e.target.checked)} />
            with the grid
          </label>
        </div>
        <NumberBox
          label={`Frame interval (ms, ${intervalHint})`}
          value={view.intervalMs}
          min={MIN_FRAME_INTERVAL_MS}
          max={MAX_FRAME_INTERVAL_MS}
          disabled={!live}
          onSet={actions.setFrameInterval}
        />
      </section>

      <section style={SECTION}>
        <label style={{ ...CHECK, fontWeight: 600 }}>
          <input type="checkbox" checked={view.motionDetection} disabled={!live} onChange={e => actions.setMotionDetection(e.target.checked)} />
          Motion detection
        </label>
        {view.motionDetection && (
          <>
            <fieldset style={{ border: 'none', margin: 0, padding: 0 }}>
              <legend style={{ fontWeight: 500, marginBottom: '0.25rem', padding: 0 }}>Sensitivity</legend>
              <div style={ROW}>
                {SENSITIVITY.map(s => (
                  <label key={s.value} style={CHECK}>
                    <input
                      type="radio"
                      name="md-sensitivity"
                      checked={ops[OP_SENSITIVITY] === s.value}
                      disabled={!live || ops[OP_SENSITIVITY] === undefined}
                      onChange={() => actions.setOp(OP_SENSITIVITY, s.value)}
                    />
                    {s.label}
                  </label>
                ))}
              </div>
            </fieldset>
            <OpBox label="Wake threshold (cells)" op={OP_THRESHOLD} value={ops[OP_THRESHOLD]} live={live} onSet={actions.setOp} />
            <OpBox label="MD interval (ms, 0 = off)" op={OP_MD_INTERVAL} value={ops[OP_MD_INTERVAL]} live={live} onSet={actions.setOp} />
            <fieldset style={{ border: 'none', margin: 0, padding: 0 }}>
              <legend style={{ fontWeight: 500, marginBottom: '0.25rem', padding: 0 }}>Grid rows</legend>
              <div style={ROW}>
                {([16, 15] as const).map(n => (
                  <label key={n} style={CHECK}>
                    <input type="radio" name="md-rows" checked={rows === n} onChange={() => onRows(n)} />
                    {n}
                  </label>
                ))}
              </div>
            </fieldset>
          </>
        )}
      </section>

      <label style={CHECK}>
        <input type="checkbox" checked={view.keepAwake} disabled={!live} onChange={e => actions.setKeepAwake(e.target.checked)} />
        Keep awake
      </label>
    </div>
  )
}

function OpBox({ label, op, value, live, onSet }: {
  label: string
  op: MotionOp
  value: number | undefined
  live: boolean
  onSet: (op: MotionOp, value: number) => void
}) {
  const [min, max] = OP_RANGE[op]
  return <NumberBox label={label} value={value} min={min} max={max} disabled={!live || value === undefined} onSet={v => onSet(op, v)} />
}

/** A number the camera holds: typed freely, sent on Enter or leaving the box, then shown as the
 *  camera reports it, so a value out of range or refused goes back to the camera's. */
function NumberBox({ label, value, min, max, disabled, onSet }: {
  label: string
  value: number | undefined
  min: number
  max: number
  disabled: boolean
  onSet: (value: number) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft === null) return
    setDraft(null)
    if (draft.trim() !== '' && Number(draft) !== value) onSet(Number(draft))
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
        disabled={disabled}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === 'Enter') commit() }}
        style={INPUT}
      />
    </label>
  )
}
