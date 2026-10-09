// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * InsightsPage — /insights
 *
 * Two sub-tabs via ?tab=reports|deployments (default reports).
 * Reports = ReportsDashboard (editable widgets). Deployments = table with a Table/Map
 * view toggle (the standalone Map gets its own home — the Field page — in P3; until
 * then it lives here as a view so nothing disappears).
 * Projects & members moved to Settings (P2).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { supabase } from '../config/supabase'
import { fetchLiveObservations } from '../lib/liveObservations'
import { DataTable, type Column } from '../components/ui/DataTable'
import { FilterSelect } from '../components/ui/ControlBar'
import { Ribbon, type RibbonGroupDef } from '../components/ui/Ribbon'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { ReportsDashboard } from '../components/data/ReportsDashboard'
import { LiveInsightsBanner } from '../components/data/LiveInsightsBanner'
import { DeploymentBulkActions } from '../components/data/DeploymentBulkActions'
import { type DeploymentRow } from '../components/data/DeploymentActionRow'
import { useUploadStore } from '../contexts/UploadContext'
import { NoProjectSelected } from '../components/common/NoProjectSelected'

interface Observation {
  id: string
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  created_at: string
}

type InsightsTab = 'reports' | 'deployments' | 'map'
type MapMetric = 'total' | 'perDay'

const TABS: { id: InsightsTab; label: string }[] = [
  { id: 'reports',     label: '📊 Reports' },
  { id: 'deployments', label: '📍 Deployments' },
  { id: 'map',         label: '🗺 Map' },
]

function formatDate(s: string | null) {
  return s ? new Date(s).toLocaleDateString() : '—'
}

// Active span of a deployment in days (min 1). Open-ended deployments run up to
// "now". Module-level so the date maths stays out of render purity checks.
function activeDaysOf(start: string | null, end: string | null): number {
  if (!start) return 1
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  return Math.max(1, Math.round((e - s) / 86_400_000))
}

const VIEW_BTN = (active: boolean): React.CSSProperties => ({
  padding: '0.3rem 0.7rem', fontSize: '0.78rem', cursor: 'pointer', whiteSpace: 'nowrap',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: active ? 'var(--primary)' : 'transparent',
  color: active ? '#fff' : 'var(--text-color)', fontWeight: active ? 600 : 400,
})

export function InsightsPage() {
  const { user } = useAuth()
  const { queryProjectIds, noProjectSelected } = useProjectSelection()
  const { isActive: uploadActive, phase: uploadPhase } = useUploadStore()
  const [searchParams, setSearchParams] = useSearchParams()

  // While an upload is processing, tick periodically so the observations query refetches
  // and partial AI results stream into the reports/map without a manual page refresh.
  const [liveTick, setLiveTick] = useState(0)
  useEffect(() => {
    if (!uploadActive) return
    const t = setInterval(() => setLiveTick(x => x + 1), 8000)
    return () => clearInterval(t)
  }, [uploadActive])

  const rawTab = searchParams.get('tab')
  const tab: InsightsTab = rawTab === 'deployments' || rawTab === 'map' ? rawTab : 'reports'
  const setTab = (t: InsightsTab) => {
    // Preserve other params (notably ?deployment=) when switching sub-tabs.
    const next = new URLSearchParams(searchParams)
    next.set('tab', t)
    setSearchParams(next, { replace: true })
  }

  // ?deployment=<uuid> — deep-link that auto-focuses the Reports tab on one
  // deployment (e.g. straight from an upload). UUID-guarded before it ever
  // reaches a query filter.
  const deploymentParam = (() => {
    const d = searchParams.get('deployment') || ''
    return /^[0-9a-fA-F-]{36}$/.test(d) ? d : ''
  })()

  const [deployments,  setDeployments]  = useState<DeploymentRow[]>([])
  const [observations, setObservations] = useState<Observation[]>([])
  const [depLoading,   setDepLoading]   = useState(false)
  const [obsLoading,   setObsLoading]   = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [selectedDepId, setSelectedDepId] = useState<string | null>(null)
  const [selectedDeps, setSelectedDeps] = useState<Set<string>>(new Set())
  const [mapMetric, setMapMetric] = useState<MapMetric>('total')
  const [mapShowAbsent, setMapShowAbsent] = useState(true)

  // Tracks the last observations-query key so the live refresh can skip the loading flash.
  const obsKeyRef = useRef('')
  // Bumped after a deployment delete/undo or location edit to refetch the list.
  const [depRefresh, setDepRefresh] = useState(0)

  const [reportFilterDep, setReportFilterDepState]    = useState(deploymentParam)
  const [reportFilterSpecies, setReportFilterSpecies] = useState('')
  const [mapFilterSpecies, setMapFilterSpecies]       = useState('')

  // The Reports ▸ Deployment filter is mirrored in the URL so the view is
  // shareable/bookmarkable and arrivals via ?deployment= land pre-focused.
  const setReportFilterDep = (id: string) => {
    setReportFilterDepState(id)
    const next = new URLSearchParams(searchParams)
    if (id) next.set('deployment', id)
    else next.delete('deployment')
    setSearchParams(next, { replace: true })
  }
  // Reflect external URL changes (back/forward, a new deep-link) into the filter.
  useEffect(() => { setReportFilterDepState(deploymentParam) }, [deploymentParam])

  // With nothing selected the page shows only a deep-linked ?deployment=, or the empty state.
  const linkOnly = noProjectSelected && !!deploymentParam
  const showNothing = noProjectSelected && !deploymentParam

  // Load deployments (both tabs use them) ──────────────────────────────────
  useEffect(() => {
    if (!user || (!queryProjectIds && !linkOnly)) return
    let cancelled = false
    setDepLoading(true)
    setError(null)

    let query = supabase
      .from('deployments')
      .select('id, project_id, location_name, latitude, longitude, deployment_start, deployment_end, created_at, projects(name), devices(name)')
      .is('deleted_at', null)
      .order('created_at', { ascending: false })

    if (!queryProjectIds) {
      query = query.eq('id', deploymentParam)
    } else if (deploymentParam) {
      // Honour the project selection, but always include a deep-linked deployment
      // even when its project isn't currently selected, so ?deployment= never
      // lands on an empty report.
      query = query.or(`project_id.in.(${queryProjectIds.join(',')}),id.eq.${deploymentParam}`)
    } else {
      query = query.in('project_id', queryProjectIds)
    }

    query.then(({ data, error: err }) => {
      if (cancelled) return
      if (err) { setError(err.message); setDepLoading(false); return }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const rows = (data || []).map((d: any) => ({
        ...d,
        project_name: d.projects?.name ?? '—',
        device_name:  d.devices?.name  ?? '—',
        projects: undefined,
        devices:  undefined,
      })) as DeploymentRow[]
      setDeployments(rows)
      setDepLoading(false)
    })
    return () => { cancelled = true }
  }, [user, queryProjectIds, linkOnly, deploymentParam, depRefresh])

  // Load observations (reports always; map view when shown) ─────────────────
  useEffect(() => {
    if (!user || showNothing) return
    if (tab !== 'reports' && tab !== 'map') return
    if (deployments.length === 0) return
    let cancelled = false
    // Only show the loading state for a genuine (re)load — not the 8s background refresh
    // while an upload classifies, which would otherwise flash "Loading…" over live data.
    const key = `${tab}|${deployments.map(d => d.id).join(',')}`
    const background = key === obsKeyRef.current
    obsKeyRef.current = key
    if (!background) setObsLoading(true)
    fetchLiveObservations<Observation>(supabase, {
      columns: 'id, deployment_id, scientific_name, observation_type, created_at',
      filter: q => q.in('deployment_id', deployments.map(d => d.id)),
    })
      .then(({ data, error: err }) => {
        if (cancelled) return
        if (!err) setObservations(data || [])
        setObsLoading(false)
      })
    return () => { cancelled = true }
    // liveTick + uploadPhase drive the live refresh while an upload is being classified.
  }, [user, showNothing, tab, deployments, liveTick, uploadPhase])

  // Map markers: per-deployment detection count (optionally for one species),
  // effort-normalised to a per-active-day rate, and present/absent flags.
  const mapMarkers = useMemo(() => {
    const counts: Record<string, number> = {}
    // Per-deployment species tally → drives the default pie-chart markers.
    const speciesByDep: Record<string, Record<string, number>> = {}
    for (const o of observations) {
      if (mapFilterSpecies && o.scientific_name !== mapFilterSpecies) continue
      counts[o.deployment_id] = (counts[o.deployment_id] ?? 0) + 1
      const sp = o.scientific_name
      if (sp && sp !== '(unidentified)') {
        const dep = (speciesByDep[o.deployment_id] ??= {})
        dep[sp] = (dep[sp] ?? 0) + 1
      }
    }
    const markers = deployments.map(d => {
      const count = counts[d.id] ?? 0
      const activeDays = activeDaysOf(d.deployment_start, d.deployment_end)
      const perDay = count / activeDays
      return {
        ...d,
        observation_count: count,
        activeDays,
        perDay,
        present: count > 0,
        metricValue: mapMetric === 'perDay' ? perDay : count,
        speciesCounts: speciesByDep[d.id] ?? {},
      }
    })
    // When a species is selected, optionally drop the "absent" sites.
    if (mapFilterSpecies && !mapShowAbsent) return markers.filter(m => m.present)
    return markers
  }, [deployments, observations, mapFilterSpecies, mapMetric, mapShowAbsent])

  // Default map focus: the most recently *finished* deployment of the selected project(s),
  // so the map opens on the latest completed survey rather than the whole-world centroid.
  // "Finished" is judged against the time the page opened: reading the clock during
  // render would give a different answer on every re-render.
  const [openedAt] = useState(Date.now)
  const defaultFocusId = useMemo(() => {
    const finished = deployments
      .filter(d => d.latitude != null && d.longitude != null && d.deployment_end &&
        new Date(d.deployment_end).getTime() <= openedAt)
      .sort((a, b) => new Date(b.deployment_end!).getTime() - new Date(a.deployment_end!).getTime())
    return finished[0]?.id ?? null
  }, [deployments, openedAt])

  const filteredObservations = useMemo(() => {
    let obs = observations
    if (reportFilterDep)     obs = obs.filter(o => o.deployment_id === reportFilterDep)
    if (reportFilterSpecies) obs = obs.filter(o => o.scientific_name === reportFilterSpecies)
    return obs
  }, [observations, reportFilterDep, reportFilterSpecies])

  const speciesOptions = useMemo(() => {
    const names = new Set<string>()
    observations.forEach(o => { if (o.scientific_name) names.add(o.scientific_name) })
    return Array.from(names).sort().map(n => ({ value: n, label: n }))
  }, [observations])

  const depFilterOptions = useMemo(() =>
    deployments.map(d => ({ value: d.id, label: d.location_name || d.id.slice(0, 8) }))
  , [deployments])

  const deploymentColumns = useMemo<Column<DeploymentRow>[]>(() => [
    { key: 'project_name', label: 'Project', cellStyle: { fontWeight: 500 } },
    { key: 'device_name',  label: 'Device' },
    {
      key: 'location_name', label: 'Location',
      render: r => r.location_name || <span style={{ opacity: 0.4 }}>—</span>,
      getValue: r => r.location_name ?? '',
    },
    {
      key: 'gps', label: 'GPS', sortable: false,
      cellStyle: { fontFamily: 'monospace', fontSize: '0.75rem' },
      render: r => r.latitude && r.longitude
        ? `${Number(r.latitude).toFixed(4)}, ${Number(r.longitude).toFixed(4)}`
        : <span style={{ opacity: 0.4 }}>—</span>,
    },
    { key: 'deployment_start', label: 'Start', cellStyle: { fontSize: '0.75rem' }, render: r => formatDate(r.deployment_start) },
    { key: 'deployment_end',   label: 'End',   cellStyle: { fontSize: '0.75rem' }, render: r => formatDate(r.deployment_end) },
  ], [])

  const ribbonGroupsFor = (id: InsightsTab): RibbonGroupDef[] => {
    if (id === 'reports') {
      return [
        { id: 'deployment', title: 'Deployment', content: (
          <FilterSelect value={reportFilterDep} onChange={setReportFilterDep} options={depFilterOptions} placeholder="All deployments" />
        ) },
        { id: 'species', title: 'Species', content: (
          <FilterSelect value={reportFilterSpecies} onChange={setReportFilterSpecies} options={speciesOptions} placeholder="All species" />
        ) },
      ]
    }
    if (id === 'map') {
      const groups: RibbonGroupDef[] = [
        { id: 'species', title: 'Species', content: (
          <FilterSelect value={mapFilterSpecies} onChange={setMapFilterSpecies} options={speciesOptions} placeholder="All species" />
        ) },
        { id: 'metric', title: 'Size by', content: (
          <div style={{ display: 'flex', gap: '0.375rem' }}>
            <button style={VIEW_BTN(mapMetric === 'total')}  onClick={() => setMapMetric('total')}>Total</button>
            <button style={VIEW_BTN(mapMetric === 'perDay')} onClick={() => setMapMetric('perDay')}>Per day</button>
          </div>
        ) },
      ]
      if (mapFilterSpecies) {
        groups.push({ id: 'absent', title: 'Absent sites', content: (
          <div style={{ display: 'flex', gap: '0.375rem' }}>
            <button style={VIEW_BTN(mapShowAbsent)}  onClick={() => setMapShowAbsent(true)}>Show</button>
            <button style={VIEW_BTN(!mapShowAbsent)} onClick={() => setMapShowAbsent(false)}>Hide</button>
          </div>
        ) })
      }
      return groups
    }
    // deployments — table only (the map lives in its own tab now)
    return [{ id: 'shown', title: 'Deployments', content: (
      <span style={{ fontSize: '0.8125rem', opacity: 0.7 }}><strong>{deployments.length}</strong> shown</span>
    ) }]
  }

  if (showNothing) return <NoProjectSelected />

  return (
    <div>
      {/* Live AI-classification banner — appears while a just-started upload is being analysed. */}
      <LiveInsightsBanner />

      <Ribbon
        activeTabId={tab}
        onTabChange={id => setTab(id as InsightsTab)}
        tabs={TABS.map(t => ({ id: t.id, label: t.label, groups: ribbonGroupsFor(t.id) }))}
      />

      {error && <p style={{ color: 'var(--error)', marginBottom: '1rem' }}>⚠ {error}</p>}

      {/* ── Reports ──────────────────────────────────────────────────── */}
      {tab === 'reports' && (
        <>
          {reportFilterSpecies && (
            <div style={{ marginBottom: '0.75rem' }}>
              <Link
                to={`/annotations?species=${encodeURIComponent(reportFilterSpecies)}`}
                style={{ fontSize: '0.82rem', color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}
                title="Open the Annotations grid pre-filtered to this species to review/correct labels"
              >
                🏷️ Review “{reportFilterSpecies}” in Annotations →
              </Link>
            </div>
          )}
          <ReportsDashboard observations={filteredObservations} deployments={deployments} loading={obsLoading} />
        </>
      )}

      {/* ── Deployments (table) ──────────────────────────────────────── */}
      {tab === 'deployments' && (
        depLoading ? (
          <p style={{ opacity: 0.5 }}>Loading deployments…</p>
        ) : (
          <>
            <DeploymentBulkActions
              selected={selectedDeps}
              rows={deployments}
              onClear={() => setSelectedDeps(new Set())}
              onShowMap={() => setTab('map')}
              onDeleted={() => { setSelectedDeps(new Set()); setDepRefresh(x => x + 1) }}
              onEdited={() => setDepRefresh(x => x + 1)}
            />
            <DataTable<DeploymentRow>
              columns={deploymentColumns}
              rows={deployments}
              rowKey={r => r.id}
              searchable
              searchPlaceholder="Search deployments…"
              exportFilename="deployments"
              emptyMessage="No deployments found for the selected project(s)."
              selectedKeys={selectedDeps}
              onSelectionChange={setSelectedDeps}
              pageSize={50}
            />
          </>
        )
      )}

      {/* ── Map ──────────────────────────────────────────────────────── */}
      {tab === 'map' && (
        depLoading ? (
          <p style={{ opacity: 0.5 }}>Loading deployments…</p>
        ) : (
          <DeploymentMap
            deployments={mapMarkers}
            selectedDeploymentId={selectedDepId}
            onSelectDeployment={setSelectedDepId}
            metric={mapMetric}
            speciesLabel={mapFilterSpecies || null}
            defaultFocusId={defaultFocusId}
            showSpeciesPie
          />
        )
      )}
    </div>
  )
}
