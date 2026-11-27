// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Realtime: /field
// LoRaWAN cameras deployed now, and those whose deployment ended today (isLorawanCamera: deployed
// with LoRaWAN on, or heard from): KPIs, a map, a "needs attention" panel, and two tabs, Cameras (a status table) and Messages (their LoRaWAN messages, live), the same
// for every role. Telemetry (last message, battery, SD free, temperature, health) comes from the
// deployment_latest_lorawan view, the ping period and firmware from the deployment, and detections
// from the cameras' own LoRaWAN counts (lib/realtimeReads.ts); each camera's state is
// lib/cameraState.ts. The Messages tab is lib/lorawanFeed.ts, live updates hooks/useFieldLive.ts.
// A project admin who picks an open camera can read or change its check-in period over LoRaWAN
// (components/data/CheckInPanel.tsx, #521). Photos stay on the camera's SD card while it is out.
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { RadioTower, TriangleAlert } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useProjectRoles } from '../hooks/useProjectRoles'
import { useFieldLive } from '../hooks/useFieldLive'
import { useMessageFeed } from '../hooks/useMessageFeed'
import { supabase } from '../config/supabase'
import {
  fetchFieldDeployments, fetchLatestTelemetry, fetchLorawanDetections, firmwareText, isLorawanCamera, realtimeKeys,
  temperatureText, NO_MESSAGE, type FieldDeployment, type Telemetry,
} from '../lib/realtimeReads'
import {
  CAMERA_STATE, cameraState, healthReasonTexts, needsAttention, notRegisteredReason, silenceText,
  LOW_BATTERY_REASON, SD_LOW_REASON, type CameraState,
} from '../lib/cameraState'
import { agoLabel, batteryText, sdFreeText } from '../lib/lorawanFeed'
import { DataTable, type Column } from '../components/ui/DataTable'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { LorawanMessages } from '../components/data/LorawanMessages'
import { CheckInPanel } from '../components/data/CheckInPanel'
import { NoProjectSelected } from '../components/common/NoProjectSelected'

// ── Types ─────────────────────────────────────────────────────────────

interface Camera extends FieldDeployment, Telemetry {
  state: CameraState
  detections48h: number         // the camera's own count, from its LoRaWAN messages
  /** In words: no DevEUI on record, the silence, then the health reasons, worst first. */
  reasons: string[]
  /** On the "needs attention" list: Silent, Fault, Warning, or no DevEUI on record (#525). */
  attention: boolean
  lowBattery: boolean
  sdLow: boolean
}

type Tab = 'cameras' | 'messages'

// ── Helpers ───────────────────────────────────────────────────────────

const LORAWAN_GUIDE = '/guides/lorawan-in-new-zealand'

const NO_DEPLOYMENTS: FieldDeployment[] = []
const NO_TELEMETRY: Record<string, Telemetry> = {}
const NO_DETECTIONS: Record<string, number> = {}
const TABS: { id: Tab; label: string }[] = [{ id: 'cameras', label: 'Cameras' }, { id: 'messages', label: 'Messages' }]

function Dot({ color }: { color: string }) {
  return (
    <span aria-hidden="true" style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: color,
    }} />
  )
}

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const NONE = <span style={MUTED}>-</span>
const BAD: React.CSSProperties = { color: '#ef4444' }

function Kpi({ label, value, tone }: { label: string; value: number | string; tone?: 'ok' | 'warn' | 'bad' }) {
  const color = tone === 'bad' ? '#ef4444' : tone === 'warn' ? '#f59e0b' : tone === 'ok' ? '#22c55e' : 'var(--text-color)'
  return (
    <div className="card" style={{ padding: '0.9rem 1.1rem', minWidth: 120, flex: 1, textAlign: 'center' }}>
      <div style={{ fontSize: '1.7rem', fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: '0.72rem', ...MUTED }}>{label}</div>
    </div>
  )
}

/** The camera's own name, as on its label (WILD-XXXX); `realtimeReads` gives '-' for none. */
const cameraName = (d: FieldDeployment) =>
  (d.device_name && d.device_name !== '-' ? d.device_name : d.location_name) || d.id.slice(0, 8)
/** The site the camera was deployed at, shown under its name. */
const siteName = (d: FieldDeployment) => (d.location_name && d.location_name !== cameraName(d) ? d.location_name : null)
const SITE: React.CSSProperties = { ...MUTED, fontWeight: 400, fontSize: '0.78rem' }

// ── Component ──────────────────────────────────────────────────────────

export function FieldPage() {
  const { user } = useAuth()
  const { queryProjectIds, noProjectSelected } = useProjectSelection()
  const roles = useProjectRoles()
  const [tab, setTab] = useState<Tab>('cameras')
  // A camera picked on the map or in the table, ringed on the map and tinted in the table.
  const [picked, setPicked] = useState<string | null>(null)
  const pick = (id: string) => setPicked(p => (p === id ? null : id))

  const depEnabled = !!user && !!queryProjectIds
  const depQuery = useQuery({
    queryKey: realtimeKeys.deployments(user?.id, queryProjectIds),
    queryFn: () => fetchFieldDeployments(supabase, queryProjectIds ?? []),
    enabled: depEnabled,
  })
  const deps = depQuery.data ?? NO_DEPLOYMENTS
  const depIds = useMemo(() => deps.map(d => d.id), [deps])

  // A refetch keeps the last answer on screen; telemetry and detections are keyed by deployment.
  const telemetryQuery = useQuery({
    queryKey: realtimeKeys.telemetry(depIds),
    queryFn: () => fetchLatestTelemetry(supabase, depIds),
    enabled: depIds.length > 0,
    placeholderData: keepPreviousData,
  })
  const detectionsQuery = useQuery({
    queryKey: realtimeKeys.detections(depIds),
    queryFn: () => fetchLorawanDetections(supabase, depIds),
    enabled: depIds.length > 0,
    placeholderData: keepPreviousData,
  })
  const feed = useMessageFeed(depIds)
  const live = useFieldLive(deps)

  const telemetry = telemetryQuery.data ?? NO_TELEMETRY
  const detections = detectionsQuery.data ?? NO_DETECTIONS
  // Which deployments are LoRaWAN cameras depends on their telemetry, so wait for both.
  const loading = depEnabled && (depQuery.isPending || (depIds.length > 0 && telemetryQuery.isPending))
  const error = (depQuery.error ?? telemetryQuery.error ?? detectionsQuery.error)?.message ?? feed.error

  // Live updates and the feed cover every deployment read, so a camera's first message brings it in.
  const cameras = useMemo<Camera[]>(() => deps.flatMap(d => {
    if (!isLorawanCamera(d, telemetry[d.id])) return []
    const t = telemetry[d.id] ?? NO_MESSAGE
    const state = cameraState({
      lastSeen: t.lastSeen, deploymentStart: d.deployment_start, deploymentEnd: d.deployment_end,
      pingMinutes: d.lorawan_ping_minutes, health: t.health,
    })
    // A finished camera has been collected: nothing about it needs a visit.
    const finished = state === 'finished'
    const notRegistered = notRegisteredReason(state, d.no_dev_eui)
    const reasons = finished ? [] : [
      ...(notRegistered ? [notRegistered] : []),
      ...(state === 'silent' ? [silenceText(d.lorawan_ping_minutes)] : []), ...healthReasonTexts(t.healthReasons),
    ]
    return [{
      ...d, ...t, state, reasons, attention: needsAttention(state) || notRegistered != null,
      detections48h: detections[d.id] ?? 0,
      lowBattery: !finished && t.healthReasons.includes(LOW_BATTERY_REASON),
      sdLow: !finished && t.healthReasons.includes(SD_LOW_REASON),
    }]
  }), [deps, telemetry, detections])
  const names = useMemo(() => new Map(deps.map(d => [d.id, cameraName(d)])), [deps])

  const troubled = useMemo(() => cameras.filter(c => c.attention), [cameras])
  const kpi = useMemo(() => ({
    active: cameras.filter(c => c.state !== 'finished').length,
    troubled: troubled.length,
    silent: cameras.filter(c => c.state === 'silent').length,
    fault: cameras.filter(c => c.state === 'fault').length,
    lowBattery: cameras.filter(c => c.lowBattery).length,
    detections: cameras.reduce((n, c) => n + c.detections48h, 0),
  }), [cameras, troubled])
  const pickedKeys = useMemo(() => (picked ? new Set([picked]) : undefined), [picked])
  // Settings go to the camera of a deployment with no end date, sent by a project admin: the
  // function's own rules (request_lorawan_downlink).
  const pickedCamera = cameras.find(c => c.id === picked)
  const settable = pickedCamera && !pickedCamera.deployment_end && roles.isProjectAdmin(pickedCamera.project_id)
    ? pickedCamera : null
  const withGps = cameras.filter(c => c.latitude != null && c.longitude != null).length
  // Only once telemetry has answered, so the notice does not flash while it loads.
  const nothingReported = telemetryQuery.isSuccess && !cameras.some(c => c.lastSeen != null)

  const columns = useMemo<Column<Camera>[]>(() => [
    { key: 'device_name', label: 'Camera', cellStyle: { fontWeight: 500 },
      render: c => {
        const site = siteName(c)
        return site ? <>{cameraName(c)}<div style={SITE}>{site}</div></> : cameraName(c)
      },
      getValue: c => [cameraName(c), siteName(c)].filter(Boolean).join(' ') },
    { key: 'project_name', label: 'Project' },
    // The state and the first reason; every reason on hover and in the attention list.
    { key: '_health', label: 'Health',
      render: c => (
        <span title={c.reasons.join('\n') || undefined} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
          <Dot color={CAMERA_STATE[c.state].color} />{CAMERA_STATE[c.state].label}
          {c.reasons.length > 0 && (
            <span style={MUTED}>{c.reasons[0]}{c.reasons.length > 1 ? ` +${c.reasons.length - 1}` : ''}</span>
          )}
        </span>
      ),
      getValue: c => [CAMERA_STATE[c.state].label, ...c.reasons].join(', ') },
    { key: 'lastSeen', label: 'Last seen', render: c => agoLabel(c.lastSeen), getValue: c => c.lastSeen ?? '' },
    { key: 'battery', label: 'Battery', cellStyle: { whiteSpace: 'nowrap' },
      render: c => c.batteryMv == null && c.batteryPct == null ? NONE
        : <span style={c.lowBattery ? BAD : undefined}>{batteryText(c)}</span>,
      getValue: batteryText },
    { key: 'sdFreePct', label: 'SD free',
      render: c => c.sdFreePct == null ? NONE : <span style={c.sdLow ? BAD : undefined}>{sdFreeText(c)}</span>,
      getValue: sdFreeText },
    { key: 'temperatureC', label: 'Temperature', cellStyle: { whiteSpace: 'nowrap' },
      render: c => (c.temperatureC == null ? NONE : temperatureText(c)), getValue: temperatureText },
    { key: 'firmware', label: 'Firmware', cellStyle: { whiteSpace: 'nowrap' },
      render: c => (c.ble_firmware || c.himax_firmware ? firmwareText(c) : NONE), getValue: firmwareText },
    { key: 'detections48h', label: 'Detections 48h',
      render: c => c.detections48h > 0 ? c.detections48h : NONE,
      getValue: c => String(c.detections48h) },
  ], [])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', margin: '0 0 0.375rem 0' }}>
        <h2 style={{ margin: 0 }}>Realtime</h2>
        {cameras.length > 0 && (
          <span role="status" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.75rem', ...MUTED }}>
            <Dot color={live ? '#22c55e' : '#9ca3af'} />{live ? 'Live' : 'Connecting'}
          </span>
        )}
      </div>
      <p style={{ ...MUTED, fontSize: '0.9rem', margin: '0 0 1rem 0' }}>
        LoRaWAN cameras deployed now or collected today, and their messages.
      </p>

      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error}</p>}

      {noProjectSelected ? (
        <NoProjectSelected />
      ) : loading ? (
        <p style={MUTED}>Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
          <RadioTower size={28} color="var(--text-muted)" aria-hidden="true" style={{ marginBottom: '0.5rem' }} />
          <div style={{ fontWeight: 600 }}>No LoRaWAN cameras deployed</div>
          <div style={{ fontSize: '0.85rem', marginTop: '0.35rem', ...MUTED }}>
            A camera in the selected projects shows here while it is deployed with LoRaWAN check-ins turned on.{' '}
            <Link to={LORAWAN_GUIDE} style={{ color: 'var(--primary)' }}>Connecting over LoRaWAN</Link>
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
                  Battery, storage, health and check-ins show after a camera&apos;s first LoRaWAN message.{' '}
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
                <Kpi label="Silent" value={kpi.silent} tone={kpi.silent ? 'bad' : undefined} />
                <Kpi label="Fault" value={kpi.fault} tone={kpi.fault ? 'bad' : undefined} />
                <Kpi label="Low battery" value={kpi.lowBattery} tone={kpi.lowBattery ? 'warn' : undefined} />
                <Kpi label="Detections 48h" value={kpi.detections} />
              </div>

              {/* Needs-attention panel */}
              {troubled.length > 0 && (
                <div style={{ marginBottom: '1.25rem', border: '1px solid rgba(239,68,68,0.35)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.5rem 0.875rem', background: 'rgba(239,68,68,0.1)', fontWeight: 600, fontSize: '0.85rem' }}>
                    <TriangleAlert size={15} color="#ef4444" aria-hidden="true" />
                    {troubled.length} camera{troubled.length !== 1 ? 's' : ''} need{troubled.length === 1 ? 's' : ''} attention
                  </div>
                  <div>
                    {troubled.map(c => (
                      <div
                        key={c.id}
                        style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', padding: '0.55rem 0.875rem', borderTop: '1px solid var(--border)', fontSize: '0.82rem' }}
                      >
                        <Dot color={CAMERA_STATE[c.state].color} />
                        <strong style={{ minWidth: 140 }}>{cameraName(c)}</strong>
                        <span style={MUTED}>{c.reasons.join(' · ')}</span>
                        <span style={{ marginLeft: 'auto', ...MUTED }}>{agoLabel(c.lastSeen)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Map, or why there is none */}
          {withGps > 0 ? (
            <div style={{ marginBottom: '1.25rem' }}>
              <DeploymentMap deployments={cameras} selectedDeploymentId={picked} onSelectDeployment={pick} />
            </div>
          ) : (
            <p style={{ ...MUTED, fontSize: '0.85rem', margin: '0 0 1.25rem 0' }}>
              No map: {cameras.length === 1
                ? 'this camera was deployed without a GPS position.'
                : 'none of these cameras was deployed with a GPS position.'}
            </p>
          )}

          {settable && (
            <CheckInPanel
              key={settable.id}
              deploymentId={settable.id}
              name={cameraName(settable)}
              pingMinutes={settable.lorawan_ping_minutes}
            />
          )}

          {/* Cameras | Messages */}
          <div role="tablist" aria-label="Realtime" style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', marginBottom: '0.875rem' }}>
            {TABS.map(t => (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`field-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`field-panel-${t.id}`}
                onClick={() => setTab(t.id)}
                style={{
                  padding: '0.5rem 0.75rem', marginBottom: -1, border: 'none', background: 'transparent', cursor: 'pointer',
                  fontSize: '0.8125rem', fontWeight: tab === t.id ? 600 : 500, whiteSpace: 'nowrap',
                  color: tab === t.id ? 'var(--primary)' : 'var(--text-muted)',
                  borderBottom: `2px solid ${tab === t.id ? 'var(--primary)' : 'transparent'}`,
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Both panels stay mounted, so each keeps its search and sort. */}
          <div role="tabpanel" id="field-panel-cameras" aria-labelledby="field-tab-cameras" hidden={tab !== 'cameras'}>
            <DataTable<Camera>
              columns={columns}
              rows={cameras}
              rowKey={c => c.id}
              searchable
              searchPlaceholder="Search cameras…"
              exportFilename="realtime-cameras"
              emptyMessage="No active cameras."
              selectedKeys={pickedKeys}
              onRowClick={c => pick(c.id)}
              pageSize={50}
            />
          </div>
          <div role="tabpanel" id="field-panel-messages" aria-labelledby="field-tab-messages" hidden={tab !== 'messages'}>
            <LorawanMessages feed={feed} names={names} />
          </div>
        </>
      )}
    </div>
  )
}
