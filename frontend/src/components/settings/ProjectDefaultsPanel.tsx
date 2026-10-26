// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProjectDefaultsPanel: one project's capture and AI defaults (Settings).
// Sets projects.capture_method_id (default triggering method), projects.model_id
// (default AI model), the burst, projects.photos_per_trigger and photo_interval_milliseconds
// (lib/burstCapture.ts), the on-device detection threshold, projects.detection_threshold_pct
// (lib/detectionThreshold.ts), and the capture flash, projects.flash_* (lib/flashSettings.ts).
// Writes are gated by RLS to project admins. RLS turns a refused update into 0 rows with no
// error, so a save asks for the row back and shows what the database holds. Members see the
// same controls switched off, so they can read the settings and ask an admin (decision 8).
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useState } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { supabase } from '../../config/supabase'
import { burstCostNote, formatInterval, photoCountOptions, photoIntervalOptions } from '../../lib/burstCapture'
import { clampThreshold, DETECTION_THRESHOLD_PCT } from '../../lib/detectionThreshold'
import {
  defaultWindow, describeUtc, FLASH_LEDS, FLASH_MODES, localOffsetMinutes, windowFromLocal, windowToLocal,
  type FlashLed, type FlashMode, type FlashWindow,
} from '../../lib/flashSettings'

interface Project {
  id: string
  name: string
  capture_method_id: number | null
  model_id: string | null
  photos_per_trigger: number
  photo_interval_milliseconds: number
  detection_threshold_pct: number
  flash_mode: FlashMode
  flash_led: FlashLed
  flash_window_start_minutes_utc: number | null
  flash_window_minutes: number | null
}

// Read once: calling Intl during render made the React compiler lint skip this component.
const BROWSER_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

const PROJECT_COLUMNS =
  'id, name, capture_method_id, model_id, photos_per_trigger, photo_interval_milliseconds, ' +
  'detection_threshold_pct, flash_mode, flash_led, flash_window_start_minutes_utc, flash_window_minutes'

function storedWindow(p: Project): FlashWindow | null {
  return p.flash_window_start_minutes_utc == null || p.flash_window_minutes == null
    ? null
    : { startUtc: p.flash_window_start_minutes_utc, minutes: p.flash_window_minutes }
}
interface CaptureMethod { id: number; value: string; description: string | null }
interface AiModel { id: string; name: string; version: string | null }

const CAPTURE_LABEL: Record<string, string> = {
  activityDetection: 'Activity detection (motion)',
  timeLapse: 'Timelapse',
}

export function ProjectDefaultsPanel({ projectId, canEdit, onSaved }: {
  projectId: string
  /** Project admin of this project (or ww_admin); everyone else gets the controls switched off. */
  canEdit: boolean
  /** Called after each save: ok, or not with the message to show. */
  onSaved: (ok: boolean, text?: string) => void
}) {
  const { user } = useAuth()
  const [project, setProject] = useState<Project | null>(null)
  const [methods, setMethods] = useState<CaptureMethod[]>([])
  const [models, setModels] = useState<AiModel[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!user) return
    let cancelled = false
    setLoading(true)
    Promise.all([
      supabase.from('projects').select(PROJECT_COLUMNS).eq('id', projectId).maybeSingle(),
      supabase.from('capture_methods').select('id, value, description').eq('is_active', true),
      // A converted or trained model is 'validated' (ready to load); 'deployed' means on a device.
      // Both are usable as a project's Species Brain.
      supabase.from('ai_models').select('id, name, version').in('status', ['validated', 'deployed']).order('name'),
    ]).then(([p, c, m]) => {
      if (cancelled) return
      setProject((p.data as Project | null) ?? null)
      setMethods((c.data as CaptureMethod[] | null) ?? [])
      setModels((m.data as AiModel[] | null) ?? [])
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [user, projectId])

  // Resolves true when the database took the change.
  const save = async (id: string, patch: Partial<Project>): Promise<boolean> => {
    const { data, error } = await supabase.from('projects').update(patch).eq('id', id).select(PROJECT_COLUMNS)
    const saved = (data as Project[] | null)?.[0]
    if (saved) {
      setProject(saved)
      onSaved(true)
    } else {
      onSaved(false, error
        ? error.code === '23514' ? `That value is not allowed: ${error.message}` : `Not saved: ${error.message}`
        : NOT_ALLOWED)
    }
    return !!saved
  }

  // The input shows what is saved: an out-of-range entry snaps into 50 to 99, and a refused save
  // puts the stored value back.
  const saveThreshold = async (p: Project, input: HTMLInputElement) => {
    const pct = clampThreshold(input.value, p.detection_threshold_pct)
    input.value = String(pct)
    if (pct === p.detection_threshold_pct) return
    if (!(await save(p.id, { detection_threshold_pct: pct }))) input.value = String(p.detection_threshold_pct)
  }

  const offset = localOffsetMinutes()

  // Leaving Time of day clears the window (the columns are null unless the mode uses them);
  // choosing it with no window fills in 18:00 to 06:00 local, so no half-set project is saved.
  const saveFlashMode = (p: Project, mode: FlashMode) => {
    const w = mode === 'time_of_day' ? storedWindow(p) ?? defaultWindow(offset) : null
    save(p.id, { flash_mode: mode, flash_window_start_minutes_utc: w?.startUtc ?? null, flash_window_minutes: w?.minutes ?? null })
  }

  const saveWindow = (p: Project, startLocal: string, endLocal: string) => {
    const w = windowFromLocal(startLocal, endLocal, offset)
    const current = storedWindow(p)
    if (!w || (current && w.startUtc === current.startUtc && w.minutes === current.minutes)) return
    save(p.id, { flash_window_start_minutes_utc: w.startUtc, flash_window_minutes: w.minutes })
  }

  if (loading) return <p style={MUTED}>Loading…</p>
  if (!project) return <p style={{ ...MUTED, fontSize: '0.85rem' }}>This project is not available to you.</p>

  const p = project
  const off = !canEdit
  const sel: React.CSSProperties = {
    padding: '0.3rem 0.45rem', fontSize: '0.8rem', border: '1px solid var(--border)',
    borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)', minWidth: 200,
    cursor: off ? 'not-allowed' : undefined,
  }

  return (
    <div>
      {off && <p style={{ ...MUTED, fontSize: '0.8125rem', margin: '0 0 0.85rem 0' }}>Only project admins can change these.</p>}
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
        <label style={FIELD}>
          <span style={MUTED}>Default triggering method</span>
          <select
            value={p.capture_method_id ?? ''}
            onChange={e => save(p.id, { capture_method_id: e.target.value ? Number(e.target.value) : null })}
            disabled={off}
            style={sel}
          >
            <option value="">Not set</option>
            {methods.map(m => (
              <option key={m.id} value={m.id}>{CAPTURE_LABEL[m.value] ?? m.description ?? m.value}</option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          <span style={MUTED}>Default AI model</span>
          <select
            value={p.model_id ?? ''}
            onChange={e => save(p.id, { model_id: e.target.value || null })}
            disabled={off}
            style={sel}
          >
            <option value="">Photos only (no AI)</option>
            {models.map(m => (
              <option key={m.id} value={m.id}>{m.name}{m.version ? ` ${m.version}` : ''}</option>
            ))}
          </select>
        </label>
        <label style={FIELD}>
          <span style={MUTED}>Photos per trigger</span>
          <select
            value={p.photos_per_trigger}
            onChange={e => save(p.id, { photos_per_trigger: Number(e.target.value) })}
            disabled={off}
            style={{ ...sel, minWidth: 90 }}
          >
            {photoCountOptions().map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label style={FIELD}>
          <span style={MUTED}>Time between photos</span>
          <select
            value={p.photo_interval_milliseconds}
            onChange={e => save(p.id, { photo_interval_milliseconds: Number(e.target.value) })}
            disabled={off || p.photos_per_trigger <= 1}
            style={{ ...sel, minWidth: 90 }}
          >
            {photoIntervalOptions(p.photo_interval_milliseconds).map(ms => <option key={ms} value={ms}>{formatInterval(ms)}</option>)}
          </select>
        </label>
      </div>
      {burstCostNote(p.photos_per_trigger) && (
        <div style={{ ...MUTED, fontSize: '0.75rem', marginTop: '0.4rem' }}>{burstCostNote(p.photos_per_trigger)}</div>
      )}
      <label
        style={{ ...FIELD, marginTop: '0.85rem' }}
        title={`Minimum confidence for the camera to record a detection. Default ${DETECTION_THRESHOLD_PCT.default}%.`}
      >
        <span style={MUTED}>Detection threshold ({DETECTION_THRESHOLD_PCT.min} to {DETECTION_THRESHOLD_PCT.max}%)</span>
        <input
          key={p.detection_threshold_pct}
          type="number"
          min={DETECTION_THRESHOLD_PCT.min}
          max={DETECTION_THRESHOLD_PCT.max}
          step={1}
          defaultValue={p.detection_threshold_pct}
          onBlur={e => saveThreshold(p, e.currentTarget)}
          onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
          disabled={off}
          style={{ ...sel, minWidth: 0, width: 90 }}
        />
      </label>
      <FlashControls p={p} sel={sel} disabled={off} offset={offset} timezone={BROWSER_TIMEZONE} onMode={saveFlashMode} onLed={led => save(p.id, { flash_led: led })} onWindow={saveWindow} />
    </div>
  )
}

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

const NOT_ALLOWED = 'You need the Project Admin role to change this.'

const FIELD: React.CSSProperties = { fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }

function FlashControls({ p, sel, disabled, offset, timezone, onMode, onLed, onWindow }: {
  p: Project
  sel: React.CSSProperties
  disabled: boolean
  offset: number
  timezone: string
  onMode: (p: Project, mode: FlashMode) => void
  onLed: (led: FlashLed) => void
  onWindow: (p: Project, startLocal: string, endLocal: string) => void
}) {
  const w = storedWindow(p)
  const local = w ? windowToLocal(w, offset) : null
  return (
    <>
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginTop: '0.85rem' }}>
        <label style={FIELD}>
          <span style={MUTED}>Capture flash</span>
          <select value={p.flash_mode} onChange={e => onMode(p, e.target.value as FlashMode)} disabled={disabled} style={{ ...sel, minWidth: 180 }}>
            {FLASH_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        {p.flash_mode !== 'off' && (
          <label style={FIELD}>
            <span style={MUTED}>LED</span>
            <select value={p.flash_led} onChange={e => onLed(e.target.value as FlashLed)} disabled={disabled} style={{ ...sel, minWidth: 90 }}>
              {FLASH_LEDS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
          </label>
        )}
        {p.flash_mode === 'time_of_day' && w && local && (
          // Keyed on the stored window so a save, or a refused one, resets the inputs.
          <div key={`${w.startUtc}-${w.minutes}`} style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-end' }}>
            <label style={FIELD}>
              <span style={MUTED}>From</span>
              <input type="time" defaultValue={local.start} onBlur={e => onWindow(p, e.target.value, local.end)} disabled={disabled} style={{ ...sel, minWidth: 0 }} />
            </label>
            <label style={FIELD}>
              <span style={MUTED}>To</span>
              <input type="time" defaultValue={local.end} onBlur={e => onWindow(p, local.start, e.target.value)} disabled={disabled} style={{ ...sel, minWidth: 0 }} />
            </label>
          </div>
        )}
      </div>
      {p.flash_mode === 'time_of_day' && w && (
        <div style={{ ...MUTED, fontSize: '0.75rem', marginTop: '0.4rem' }}>
          Times are {timezone}. The camera runs on UTC: {describeUtc(w)}.
        </div>
      )}
      {p.flash_mode === 'off' && (
        <div style={{ ...MUTED, fontSize: '0.75rem', marginTop: '0.4rem' }}>
          Off also turns off the night IR light for motion detection.
        </div>
      )}
    </>
  )
}
