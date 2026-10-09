// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProjectDefaultsPanel — per-project capture + AI defaults (Settings).
// Sets projects.capture_method_id (default triggering method), projects.model_id
// (default AI model), the burst, projects.photos_per_trigger and photo_interval_milliseconds
// (lib/burstCapture.ts), the on-device detection threshold, projects.detection_threshold_pct
// (lib/detectionThreshold.ts), and the capture flash, projects.flash_* (lib/flashSettings.ts).
// Writes are gated by RLS to project admins. RLS turns a refused update into 0 rows with no
// error, so a save asks for the row back and shows what the database holds.
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

export function ProjectDefaultsPanel({ projectId }: { projectId?: string } = {}) {
  const { user } = useAuth()
  const [projects, setProjects] = useState<Project[]>([])
  const [methods, setMethods] = useState<CaptureMethod[]>([])
  const [models, setModels] = useState<AiModel[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!user) return
    let cancelled = false
    setLoading(true)
    // Scope to one project when opened as a per-project action; otherwise list all.
    let projQuery = supabase.from('projects').select(PROJECT_COLUMNS).order('name')
    if (projectId) projQuery = projQuery.eq('id', projectId)
    Promise.all([
      projQuery,
      supabase.from('capture_methods').select('id, value, description').eq('is_active', true),
      // A converted or trained model is 'validated' (ready to load); 'deployed' means on a device.
      // Both are usable as a project's Species Brain.
      supabase.from('ai_models').select('id, name, version').in('status', ['validated', 'deployed']).order('name'),
    ]).then(([p, c, m]) => {
      if (cancelled) return
      setProjects((p.data as Project[] | null) ?? [])
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
    if (saved) setProjects(prev => prev.map(p => (p.id === id ? saved : p)))
    const text = error
      ? error.code === '23514' ? `That value is not allowed: ${error.message}` : `Not saved: ${error.message}`
      : saved ? 'Saved ✓' : 'You need the Project Admin role to change this.'
    setMsg(m => ({ ...m, [id]: text }))
    setTimeout(() => setMsg(m => ({ ...m, [id]: '' })), 2500)
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

  if (loading) return <p style={{ opacity: 0.5 }}>Loading…</p>
  if (projects.length === 0) return <p style={{ fontSize: '0.85rem', opacity: 0.65 }}>No projects to configure yet.</p>

  const sel: React.CSSProperties = {
    padding: '0.3rem 0.45rem', fontSize: '0.8rem', border: '1px solid var(--border)',
    borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)', minWidth: 200,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
      {projects.map(p => (
        <div key={p.id} style={{ border: projectId ? 'none' : '1px solid var(--border)', borderRadius: 'var(--radius)', padding: projectId ? 0 : '0.75rem 0.9rem' }}>
          {/* The slide-over header already names the project when scoped to one. */}
          {!projectId && <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '0.5rem' }}>{p.name}</div>}
          <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Default triggering method</span>
              <select
                value={p.capture_method_id ?? ''}
                onChange={e => save(p.id, { capture_method_id: e.target.value ? Number(e.target.value) : null })}
                style={sel}
              >
                <option value="">— Not set —</option>
                {methods.map(m => (
                  <option key={m.id} value={m.id}>{CAPTURE_LABEL[m.value] ?? m.description ?? m.value}</option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Default AI model</span>
              <select
                value={p.model_id ?? ''}
                onChange={e => save(p.id, { model_id: e.target.value || null })}
                style={sel}
              >
                <option value="">— Photos only (no AI) —</option>
                {models.map(m => (
                  <option key={m.id} value={m.id}>{m.name}{m.version ? ` ${m.version}` : ''}</option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Photos per trigger</span>
              <select
                value={p.photos_per_trigger}
                onChange={e => save(p.id, { photos_per_trigger: Number(e.target.value) })}
                style={{ ...sel, minWidth: 90 }}
              >
                {photoCountOptions().map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <span style={{ opacity: 0.7 }}>Time between photos</span>
              <select
                value={p.photo_interval_milliseconds}
                onChange={e => save(p.id, { photo_interval_milliseconds: Number(e.target.value) })}
                disabled={p.photos_per_trigger <= 1}
                style={{ ...sel, minWidth: 90 }}
              >
                {photoIntervalOptions(p.photo_interval_milliseconds).map(ms => <option key={ms} value={ms}>{formatInterval(ms)}</option>)}
              </select>
            </label>
          </div>
          {burstCostNote(p.photos_per_trigger) && (
            <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '0.4rem' }}>{burstCostNote(p.photos_per_trigger)}</div>
          )}
          <label style={{ ...FIELD, marginTop: '0.85rem' }}>
            <span style={{ opacity: 0.7 }}>Detection threshold (%)</span>
            <input
              key={p.detection_threshold_pct}
              type="number"
              min={DETECTION_THRESHOLD_PCT.min}
              max={DETECTION_THRESHOLD_PCT.max}
              step={1}
              defaultValue={p.detection_threshold_pct}
              onBlur={e => saveThreshold(p, e.currentTarget)}
              onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
              style={{ ...sel, minWidth: 0, width: 90 }}
            />
          </label>
          <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '0.4rem' }}>
            Minimum confidence for the camera to record a detection, {DETECTION_THRESHOLD_PCT.min} to {DETECTION_THRESHOLD_PCT.max}. Default {DETECTION_THRESHOLD_PCT.default}%.
          </div>
          <FlashControls p={p} sel={sel} offset={offset} timezone={BROWSER_TIMEZONE} onMode={saveFlashMode} onLed={led => save(p.id, { flash_led: led })} onWindow={saveWindow} />
          {msg[p.id] && (
            <div style={{ fontSize: '0.72rem', marginTop: '0.4rem', color: msg[p.id].startsWith('Saved') ? 'var(--success)' : 'var(--error)' }}>
              {msg[p.id]}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

const FIELD: React.CSSProperties = { fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }

function FlashControls({ p, sel, offset, timezone, onMode, onLed, onWindow }: {
  p: Project
  sel: React.CSSProperties
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
          <span style={{ opacity: 0.7 }}>Capture flash</span>
          <select value={p.flash_mode} onChange={e => onMode(p, e.target.value as FlashMode)} style={{ ...sel, minWidth: 180 }}>
            {FLASH_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        {p.flash_mode !== 'off' && (
          <label style={FIELD}>
            <span style={{ opacity: 0.7 }}>LED</span>
            <select value={p.flash_led} onChange={e => onLed(e.target.value as FlashLed)} style={{ ...sel, minWidth: 90 }}>
              {FLASH_LEDS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
          </label>
        )}
        {p.flash_mode === 'time_of_day' && w && local && (
          // Keyed on the stored window so a save, or a refused one, resets the inputs.
          <div key={`${w.startUtc}-${w.minutes}`} style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-end' }}>
            <label style={FIELD}>
              <span style={{ opacity: 0.7 }}>From</span>
              <input type="time" defaultValue={local.start} onBlur={e => onWindow(p, e.target.value, local.end)} style={{ ...sel, minWidth: 0 }} />
            </label>
            <label style={FIELD}>
              <span style={{ opacity: 0.7 }}>To</span>
              <input type="time" defaultValue={local.end} onBlur={e => onWindow(p, local.start, e.target.value)} style={{ ...sel, minWidth: 0 }} />
            </label>
          </div>
        )}
      </div>
      {p.flash_mode === 'time_of_day' && w && (
        <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '0.4rem' }}>
          Times are {timezone}. The camera runs on UTC: {describeUtc(w)}.
        </div>
      )}
      {p.flash_mode === 'off' && (
        <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '0.4rem' }}>
          Off also turns off the night IR light for motion detection.
        </div>
      )}
    </>
  )
}
