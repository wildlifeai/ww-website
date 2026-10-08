// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Realtime — /field
// Operations cockpit for cameras currently in the field: live KPIs, a map, a
// "needs attention" panel, and a status table. Telemetry (battery, SD usage,
// last check-in) comes from the LoRaWAN tables; recent detections from
// observations. All real — panels simply read "awaiting check-in" until a
// camera's first LoRaWAN message arrives.
/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { supabase } from '../config/supabase'
import { DataTable, type Column } from '../components/ui/DataTable'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { NoProjectSelected } from '../components/common/NoProjectSelected'

// ── Types ─────────────────────────────────────────────────────────────

interface ActiveDeployment {
  id: string
  project_id: string
  device_id: string | null
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  project_name: string
  device_name: string
}

type Freshness = 'fresh' | 'stale' | 'silent' | 'awaiting'

interface Camera extends ActiveDeployment {
  battery: number | null        // %
  sdUsed: number | null         // %
  lastSeen: string | null       // last LoRaWAN check-in
  freshness: Freshness
  detections24h: number
  lastDetection: string | null
  topSpecies: string | null
  troubled: boolean
  reasons: string[]
}

// ── Helpers ───────────────────────────────────────────────────────────

const LOW_BATTERY = 20
const FULL_SD = 90

function freshnessOf(lastSeen: string | null): Freshness {
  if (!lastSeen) return 'awaiting'
  const hours = (Date.now() - new Date(lastSeen).getTime()) / 3_600_000
  if (hours <= 24) return 'fresh'
  if (hours <= 72) return 'stale'
  return 'silent'
}

function agoLabel(iso: string | null): string {
  if (!iso) return '—'
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (mins < 60) return `${mins}m ago`
  const h = Math.round(mins / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

const FRESH_DOT: Record<Freshness, { color: string; label: string }> = {
  fresh:    { color: '#22c55e', label: '🟢 Fresh' },
  stale:    { color: '#f59e0b', label: '🟠 Stale' },
  silent:   { color: '#ef4444', label: '🔴 Silent' },
  awaiting: { color: '#9ca3af', label: '⚪ Awaiting' },
}

function Kpi({ label, value, tone }: { label: string; value: number | string; tone?: 'ok' | 'warn' | 'bad' }) {
  const color = tone === 'bad' ? '#ef4444' : tone === 'warn' ? '#f59e0b' : tone === 'ok' ? '#22c55e' : 'var(--text-color)'
  return (
    <div className="glass-card" style={{ padding: '0.9rem 1.1rem', minWidth: 120, flex: 1, textAlign: 'center' }}>
      <div style={{ fontSize: '1.7rem', fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: '0.72rem', opacity: 0.7 }}>{label}</div>
    </div>
  )
}

// ── Component ──────────────────────────────────────────────────────────

export function FieldPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { queryProjectIds, noProjectSelected } = useProjectSelection()
  const [deps, setDeps] = useState<ActiveDeployment[]>([])
  const [telemetry, setTelemetry] = useState<Record<string, { battery: number | null; sdUsed: number | null; lastSeen: string | null }>>({})
  const [detections, setDetections] = useState<Record<string, { count: number; latest: string | null; top: string | null }>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Active deployments (no end date, or an end date in the future).
  useEffect(() => {
    if (!user || !queryProjectIds) return
    let cancelled = false
    setLoading(true); setError(null)
    const nowIso = new Date().toISOString()
    const query = supabase
      .from('deployments')
      .select('id, project_id, device_id, location_name, latitude, longitude, deployment_start, deployment_end, projects(name), devices(name)')
      .is('deleted_at', null)
      .or(`deployment_end.is.null,deployment_end.gt.${nowIso}`)
      .order('deployment_start', { ascending: true })
      .in('project_id', queryProjectIds)

    query.then(({ data, error: err }) => {
      if (cancelled) return
      if (err) { setError(err.message); setLoading(false); return }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const mapped = (data || []).map((d: any) => ({
        id: d.id, project_id: d.project_id, device_id: d.device_id,
        location_name: d.location_name, latitude: d.latitude, longitude: d.longitude,
        deployment_start: d.deployment_start, deployment_end: d.deployment_end,
        project_name: d.projects?.name ?? '—', device_name: d.devices?.name ?? '—',
      })) as ActiveDeployment[]
      setDeps(mapped)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [user, queryProjectIds])

  // Live updates: any new LoRaWAN message re-runs the telemetry/detections
  // fetch below. Requires lorawan_messages in the supabase_realtime
  // publication (migration 20260711043000 in ww-backend); until that is
  // applied the page just behaves as before (fetch on load).
  const [telemetryRefresh, setTelemetryRefresh] = useState(0)
  useEffect(() => {
    // No listening while nothing is selected: `deps` still holds the last selection's cameras.
    if (!user || !queryProjectIds || deps.length === 0) return
    // Realtime already enforces RLS (only rows this user can read are
    // delivered); the deployment filter narrows further to the deployments
    // this page is showing, so messages for other projects — or bench
    // messages with no deployment yet — don't trigger a refetch.
    const depIds = new Set(deps.map(d => d.id))
    const channel = supabase
      .channel('field-lorawan-live')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'lorawan_messages' },
        (payload) => {
          const row = payload.new as { deployment_id: string | null }
          if (row.deployment_id && depIds.has(row.deployment_id)) {
            setTelemetryRefresh(n => n + 1)
          }
        })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [user, queryProjectIds, deps])

  // Latest LoRaWAN telemetry per deployment + recent detections.
  useEffect(() => {
    if (deps.length === 0) { setTelemetry({}); setDetections({}); return }
    let cancelled = false
    const depIds = deps.map(d => d.id)

    // Telemetry: newest message per deployment wins (rows arrive newest-first).
    supabase
      .from('lorawan_messages')
      .select('deployment_id, received_at, lorawan_parsed_messages(battery_level, sd_card_used_capacity)')
      .in('deployment_id', depIds)
      .order('received_at', { ascending: false })
      .limit(1000)
      .then(({ data }) => {
        if (cancelled) return
        const t: Record<string, { battery: number | null; sdUsed: number | null; lastSeen: string | null }> = {}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        for (const m of (data || []) as any[]) {
          if (!m.deployment_id || t[m.deployment_id]) continue // first = newest
          const p = Array.isArray(m.lorawan_parsed_messages) ? m.lorawan_parsed_messages[0] : m.lorawan_parsed_messages
          t[m.deployment_id] = { battery: p?.battery_level ?? null, sdUsed: p?.sd_card_used_capacity ?? null, lastSeen: m.received_at ?? null }
        }
        setTelemetry(t)
      })

    // Detections in the last 48h.
    const since = new Date(Date.now() - 48 * 3_600_000).toISOString()
    supabase
      .from('observations')
      .select('deployment_id, scientific_name, created_at')
      .in('deployment_id', depIds)
      .eq('observation_type', 'animal')
      .gte('created_at', since)
      .is('deleted_at', null)
      .then(({ data }) => {
        if (cancelled) return
        const d: Record<string, { count: number; latest: string | null; top: string | null }> = {}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        for (const o of (data || []) as any[]) {
          const e = d[o.deployment_id] || { count: 0, latest: null, top: null }
          e.count += 1
          if (!e.latest || o.created_at > e.latest) { e.latest = o.created_at; e.top = o.scientific_name ?? e.top }
          d[o.deployment_id] = e
        }
        setDetections(d)
      })
    return () => { cancelled = true }
  }, [deps, telemetryRefresh])

  const cameras = useMemo<Camera[]>(() => deps.map(d => {
    const t = telemetry[d.id] ?? { battery: null, sdUsed: null, lastSeen: null }
    const det = detections[d.id] ?? { count: 0, latest: null, top: null }
    const freshness = freshnessOf(t.lastSeen)
    const reasons: string[] = []
    if (freshness === 'silent') reasons.push('No check-in >3 days')
    if (t.battery != null && t.battery < LOW_BATTERY) reasons.push(`Low battery (${t.battery}%)`)
    if (t.sdUsed != null && t.sdUsed > FULL_SD) reasons.push(`SD almost full (${t.sdUsed}%)`)
    return {
      ...d, battery: t.battery, sdUsed: t.sdUsed, lastSeen: t.lastSeen, freshness,
      detections24h: det.count, lastDetection: det.latest, topSpecies: det.top,
      troubled: reasons.length > 0, reasons,
    }
  }), [deps, telemetry, detections])

  const kpi = useMemo(() => {
    const troubled = cameras.filter(c => c.troubled)
    return {
      active: cameras.length,
      fresh: cameras.filter(c => c.freshness === 'fresh').length,
      silent: cameras.filter(c => c.freshness === 'silent').length,
      troubled: troubled.length,
      lowBattery: cameras.filter(c => c.battery != null && c.battery < LOW_BATTERY).length,
      detections: cameras.reduce((n, c) => n + c.detections24h, 0),
      hasTelemetry: cameras.some(c => c.lastSeen != null),
    }
  }, [cameras])

  const troubled = useMemo(() => cameras.filter(c => c.troubled), [cameras])
  const withGps = cameras.filter(c => c.latitude != null && c.longitude != null).length

  const columns = useMemo<Column<Camera>[]>(() => [
    { key: 'location_name', label: 'Camera', cellStyle: { fontWeight: 500 },
      render: c => c.location_name || c.device_name || c.id.slice(0, 8), getValue: c => c.location_name ?? '' },
    { key: 'project_name', label: 'Project' },
    { key: '_status', label: 'Check-in', sortable: false,
      render: c => <span style={{ color: FRESH_DOT[c.freshness].color }}>{FRESH_DOT[c.freshness].label}</span> },
    { key: 'lastSeen', label: 'Last seen', render: c => agoLabel(c.lastSeen), getValue: c => c.lastSeen ?? '' },
    { key: 'battery', label: 'Battery',
      render: c => c.battery == null ? <span style={{ opacity: 0.4 }}>—</span>
        : <span style={{ color: c.battery < LOW_BATTERY ? '#ef4444' : undefined }}>{c.battery}%</span>,
      getValue: c => String(c.battery ?? -1) },
    { key: 'sdUsed', label: 'SD used',
      render: c => c.sdUsed == null ? <span style={{ opacity: 0.4 }}>—</span>
        : <span style={{ color: c.sdUsed > FULL_SD ? '#ef4444' : undefined }}>{c.sdUsed}%</span>,
      getValue: c => String(c.sdUsed ?? -1) },
    { key: 'detections24h', label: 'Detections 48h',
      render: c => c.detections24h > 0
        ? <span>{c.detections24h}{c.topSpecies ? ` · ${c.topSpecies}` : ''}</span>
        : <span style={{ opacity: 0.4 }}>—</span>,
      getValue: c => String(c.detections24h) },
  ], [])

  return (
    <div>
      <h2 style={{ margin: '0 0 0.375rem 0' }}>📡 Realtime</h2>
      <p style={{ opacity: 0.65, fontSize: '0.9rem', margin: '0 0 1rem 0' }}>
        Operations cockpit for cameras out in the field.
      </p>

      {error && <p style={{ color: 'var(--error)', marginBottom: '1rem' }}>⚠ {error}</p>}

      {noProjectSelected ? (
        <NoProjectSelected />
      ) : loading ? (
        <p style={{ opacity: 0.5 }}>Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', opacity: 0.7 }}>
          <div style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>🏕️</div>
          <div style={{ fontWeight: 600 }}>No active deployments</div>
          <div style={{ fontSize: '0.85rem', opacity: 0.65, marginTop: '0.35rem' }}>
            Cameras appear here while they're deployed in the field.
          </div>
        </div>
      ) : (
        <>
          {/* KPI strip */}
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1.25rem' }}>
            <Kpi label="Active cameras" value={kpi.active} />
            <Kpi label="Needs attention" value={kpi.troubled} tone={kpi.troubled ? 'bad' : 'ok'} />
            <Kpi label="Silent (>3d)" value={kpi.silent} tone={kpi.silent ? 'bad' : undefined} />
            <Kpi label="Low battery" value={kpi.lowBattery} tone={kpi.lowBattery ? 'warn' : undefined} />
            <Kpi label="Fresh check-in" value={kpi.fresh} tone={kpi.fresh ? 'ok' : undefined} />
            <Kpi label="Detections 48h" value={kpi.detections} />
          </div>

          {/* Needs-attention panel */}
          {troubled.length > 0 ? (
            <div style={{ marginBottom: '1.25rem', border: '1px solid rgba(239,68,68,0.35)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
              <div style={{ padding: '0.5rem 0.875rem', background: 'rgba(239,68,68,0.1)', fontWeight: 600, fontSize: '0.85rem' }}>
                ⚠ {troubled.length} camera{troubled.length !== 1 ? 's' : ''} need attention
              </div>
              <div>
                {troubled.map(c => (
                  <button
                    key={c.id}
                    onClick={() => navigate(`/annotations?deployment=${c.id}`)}
                    style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', width: '100%', textAlign: 'left', padding: '0.55rem 0.875rem', background: 'transparent', border: 'none', borderTop: '1px solid var(--border)', cursor: 'pointer', color: 'var(--text-color)', fontSize: '0.82rem' }}
                  >
                    <span style={{ color: FRESH_DOT[c.freshness].color }}>●</span>
                    <strong style={{ minWidth: 140 }}>{c.location_name || c.device_name || c.id.slice(0, 8)}</strong>
                    <span style={{ opacity: 0.7 }}>{c.reasons.join(' · ')}</span>
                    <span style={{ marginLeft: 'auto', opacity: 0.5 }}>{agoLabel(c.lastSeen)}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : !kpi.hasTelemetry && (
            <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-start', padding: '0.7rem 0.9rem', marginBottom: '1.25rem', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 'var(--radius)', fontSize: '0.8rem' }}>
              <span style={{ fontSize: '1rem' }}>ℹ️</span>
              <span style={{ opacity: 0.85, lineHeight: 1.5 }}>
                Cameras are <strong>active</strong> by deployment dates. LoRaWAN check-ins aren't flowing
                yet — battery, storage and check-in freshness fill in automatically once a camera reports in.
              </span>
            </div>
          )}

          {/* Map */}
          {withGps > 0 && (
            <div style={{ marginBottom: '1.25rem' }}>
              <DeploymentMap deployments={cameras} selectedDeploymentId={null} onSelectDeployment={(id) => navigate(`/annotations?deployment=${id}`)} />
            </div>
          )}

          {/* Status table */}
          <DataTable<Camera>
            columns={columns}
            rows={cameras}
            rowKey={c => c.id}
            searchable
            searchPlaceholder="Search cameras…"
            exportFilename="realtime-cameras"
            emptyMessage="No active cameras."
            onRowClick={c => navigate(`/annotations?deployment=${c.id}`)}
            pageSize={50}
          />
        </>
      )}
    </div>
  )
}
