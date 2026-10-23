// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Realtime: /field
// Cameras deployed now: KPIs, a map, a "needs attention" panel and a status table, read-only for
// every role. Telemetry (battery, SD usage, last check-in) comes from the LoRaWAN tables, recent
// detections from observations; the reads are in lib/realtimeReads.ts.
import { useEffect, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { RadioTower, TriangleAlert } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { supabase } from '../config/supabase'
import {
  fetchActiveDeployments, fetchLatestTelemetry, fetchRecentDetections,
  type ActiveDeployment, type RecentDetections, type Telemetry,
} from '../lib/realtimeReads'
import { DataTable, type Column } from '../components/ui/DataTable'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { NoProjectSelected } from '../components/common/NoProjectSelected'

// ── Types ─────────────────────────────────────────────────────────────

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
const LORAWAN_GUIDE = '/guides/lorawan-in-new-zealand'

const NO_DEPLOYMENTS: ActiveDeployment[] = []
const NO_TELEMETRY: Record<string, Telemetry> = {}
const NO_DETECTIONS: Record<string, RecentDetections> = {}

function freshnessOf(lastSeen: string | null): Freshness {
  if (!lastSeen) return 'awaiting'
  const hours = (Date.now() - new Date(lastSeen).getTime()) / 3_600_000
  if (hours <= 24) return 'fresh'
  if (hours <= 72) return 'stale'
  return 'silent'
}

function agoLabel(iso: string | null): string {
  if (!iso) return '-'
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (mins < 60) return `${mins}m ago`
  const h = Math.round(mins / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

const FRESHNESS: Record<Freshness, { color: string; label: string }> = {
  fresh:    { color: '#22c55e', label: 'Fresh' },
  stale:    { color: '#f59e0b', label: 'Stale' },
  silent:   { color: '#ef4444', label: 'Silent' },
  awaiting: { color: '#9ca3af', label: 'Awaiting' },
}

function Dot({ freshness }: { freshness: Freshness }) {
  return (
    <span aria-hidden="true" style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
      background: FRESHNESS[freshness].color,
    }} />
  )
}

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const NONE = <span style={MUTED}>-</span>

function Kpi({ label, value, tone }: { label: string; value: number | string; tone?: 'ok' | 'warn' | 'bad' }) {
  const color = tone === 'bad' ? '#ef4444' : tone === 'warn' ? '#f59e0b' : tone === 'ok' ? '#22c55e' : 'var(--text-color)'
  return (
    <div className="card" style={{ padding: '0.9rem 1.1rem', minWidth: 120, flex: 1, textAlign: 'center' }}>
      <div style={{ fontSize: '1.7rem', fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: '0.72rem', ...MUTED }}>{label}</div>
    </div>
  )
}

// ── Component ──────────────────────────────────────────────────────────

export function FieldPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { queryProjectIds, noProjectSelected } = useProjectSelection()

  const depEnabled = !!user && !!queryProjectIds
  const depQuery = useQuery({
    queryKey: ['realtime', 'deployments', user?.id, queryProjectIds],
    queryFn: () => fetchActiveDeployments(supabase, queryProjectIds ?? []),
    enabled: depEnabled,
  })
  const deps = depQuery.data ?? NO_DEPLOYMENTS
  const depIds = useMemo(() => deps.map(d => d.id), [deps])

  // A refetch keeps the last answer on screen; telemetry and detections are keyed by deployment.
  const telemetryQuery = useQuery({
    queryKey: ['realtime', 'telemetry', depIds],
    queryFn: () => fetchLatestTelemetry(supabase, depIds),
    enabled: depIds.length > 0,
    placeholderData: keepPreviousData,
  })
  const detectionsQuery = useQuery({
    queryKey: ['realtime', 'detections', depIds],
    queryFn: () => fetchRecentDetections(supabase, depIds),
    enabled: depIds.length > 0,
    placeholderData: keepPreviousData,
  })
  const telemetry = telemetryQuery.data ?? NO_TELEMETRY
  const detections = detectionsQuery.data ?? NO_DETECTIONS
  const loading = depEnabled && depQuery.isPending
  const error = (depQuery.error ?? telemetryQuery.error ?? detectionsQuery.error)?.message ?? null

  // Live updates: a new LoRaWAN message for a shown deployment reads telemetry and detections
  // again. Needs lorawan_messages in the supabase_realtime publication (ww-backend migration
  // 20260711043000); without it the page reads on load only.
  useEffect(() => {
    // No listening while nothing is selected or nothing is shown.
    if (!user || !queryProjectIds || depIds.length === 0) return
    // Realtime already enforces RLS; the filter narrows to the deployments this page shows, so
    // messages for other projects, or bench messages with no deployment yet, read nothing.
    const shown = new Set(depIds)
    const channel = supabase
      .channel('field-lorawan-live')
      .on('postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'lorawan_messages' },
        (payload) => {
          const row = payload.new as { deployment_id: string | null }
          if (row.deployment_id && shown.has(row.deployment_id)) {
            void queryClient.invalidateQueries({ queryKey: ['realtime', 'telemetry'] })
            void queryClient.invalidateQueries({ queryKey: ['realtime', 'detections'] })
          }
        })
      .subscribe()
    return () => { void supabase.removeChannel(channel) }
  }, [user, queryProjectIds, depIds, queryClient])

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

  const kpi = useMemo(() => ({
    active: cameras.length,
    fresh: cameras.filter(c => c.freshness === 'fresh').length,
    silent: cameras.filter(c => c.freshness === 'silent').length,
    troubled: cameras.filter(c => c.troubled).length,
    lowBattery: cameras.filter(c => c.battery != null && c.battery < LOW_BATTERY).length,
    detections: cameras.reduce((n, c) => n + c.detections24h, 0),
  }), [cameras])

  const troubled = useMemo(() => cameras.filter(c => c.troubled), [cameras])
  const withGps = cameras.filter(c => c.latitude != null && c.longitude != null).length
  // Only once telemetry has answered, so the notice does not flash while it loads.
  const nothingReported = telemetryQuery.isSuccess && !cameras.some(c => c.lastSeen != null)

  const columns = useMemo<Column<Camera>[]>(() => [
    { key: 'location_name', label: 'Camera', cellStyle: { fontWeight: 500 },
      render: c => c.location_name || c.device_name || c.id.slice(0, 8), getValue: c => c.location_name ?? '' },
    { key: 'project_name', label: 'Project' },
    { key: '_status', label: 'Check-in', sortable: false,
      render: c => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
          <Dot freshness={c.freshness} />{FRESHNESS[c.freshness].label}
        </span>
      ),
      getValue: c => FRESHNESS[c.freshness].label },
    { key: 'lastSeen', label: 'Last seen', render: c => agoLabel(c.lastSeen), getValue: c => c.lastSeen ?? '' },
    { key: 'battery', label: 'Battery',
      render: c => c.battery == null ? NONE
        : <span style={{ color: c.battery < LOW_BATTERY ? '#ef4444' : undefined }}>{c.battery}%</span>,
      getValue: c => String(c.battery ?? -1) },
    { key: 'sdUsed', label: 'SD used',
      render: c => c.sdUsed == null ? NONE
        : <span style={{ color: c.sdUsed > FULL_SD ? '#ef4444' : undefined }}>{c.sdUsed}%</span>,
      getValue: c => String(c.sdUsed ?? -1) },
    { key: 'detections24h', label: 'Detections 48h',
      render: c => c.detections24h > 0
        ? <span>{c.detections24h}{c.topSpecies ? ` · ${c.topSpecies}` : ''}</span>
        : NONE,
      getValue: c => String(c.detections24h) },
  ], [])

  return (
    <div>
      <h2 style={{ margin: '0 0 0.375rem 0' }}>Realtime</h2>
      <p style={{ ...MUTED, fontSize: '0.9rem', margin: '0 0 1rem 0' }}>
        Cameras deployed now, with their last LoRaWAN check-in.
      </p>

      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error}</p>}

      {noProjectSelected ? (
        <NoProjectSelected />
      ) : loading ? (
        <p style={MUTED}>Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
          <RadioTower size={28} color="var(--text-muted)" aria-hidden="true" style={{ marginBottom: '0.5rem' }} />
          <div style={{ fontWeight: 600 }}>No active deployments</div>
          <div style={{ fontSize: '0.85rem', marginTop: '0.35rem', ...MUTED }}>
            A camera shows here while it is deployed.
          </div>
        </div>
      ) : (
        <>
          {nothingReported ? (
            <div className="card" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '1rem 1.25rem', marginBottom: '1.25rem' }}>
              <RadioTower size={22} color="var(--text-muted)" aria-hidden="true" style={{ flexShrink: 0 }} />
              <div style={{ fontSize: '0.875rem' }}>
                <div style={{ fontWeight: 600 }}>No camera has checked in yet</div>
                <div style={MUTED}>
                  Battery, storage and check-ins show after a camera&apos;s first LoRaWAN message.{' '}
                  <Link to={LORAWAN_GUIDE} style={{ color: 'var(--primary)' }}>Connecting over LoRaWAN</Link>
                </div>
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
              {troubled.length > 0 && (
                <div style={{ marginBottom: '1.25rem', border: '1px solid rgba(239,68,68,0.35)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.875rem', background: 'rgba(239,68,68,0.1)', fontWeight: 600, fontSize: '0.85rem' }}>
                    <TriangleAlert size={15} color="#ef4444" aria-hidden="true" />
                    {troubled.length} camera{troubled.length !== 1 ? 's' : ''} need attention
                  </div>
                  <div>
                    {troubled.map(c => (
                      <button
                        key={c.id}
                        onClick={() => navigate(`/annotations?deployment=${c.id}`)}
                        style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', width: '100%', textAlign: 'left', padding: '0.55rem 0.875rem', background: 'transparent', border: 'none', borderTop: '1px solid var(--border)', cursor: 'pointer', color: 'var(--text-color)', fontSize: '0.82rem' }}
                      >
                        <Dot freshness={c.freshness} />
                        <strong style={{ minWidth: 140 }}>{c.location_name || c.device_name || c.id.slice(0, 8)}</strong>
                        <span style={MUTED}>{c.reasons.join(' · ')}</span>
                        <span style={{ marginLeft: 'auto', ...MUTED }}>{agoLabel(c.lastSeen)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </>
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
