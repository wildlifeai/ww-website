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
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
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

const NO_DEPLOYMENTS: DeploymentRow[] = []
const NO_OBSERVATIONS: Observation[] = []

/**
 * The selected projects' deployments. A deep-linked ?deployment= is always included, even
 * when its project isn't selected, so it never lands on an empty report; with no project
 * ids it is the only one.
 */
async function fetchDeployments(projectIds: string[] | null, deploymentParam: string): Promise<DeploymentRow[]> {
  let query = supabase
    .from('deployments')
    .select('id, project_id, location_name, latitude, longitude, deployment_start, deployment_end, created_at, projects(name), devices(name)')
    .is('deleted_at', null)
    .order('created_at', { ascending: false })

  if (!projectIds) {
    query = query.eq('id', deploymentParam)
  } else if (deploymentParam) {
    query = query.or(`project_id.in.(${projectIds.join(',')}),id.eq.${deploymentParam}`)
  } else {
    query = query.in('project_id', projectIds)
  }

  const { data, error } = await query
  if (error) throw new Error(error.message)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data || []).map((d: any) => ({
    ...d,
    project_name: d.projects?.name ?? '—',
    device_name:  d.devices?.name  ?? '—',
    projects: undefined,
    devices:  undefined,
  })) as DeploymentRow[]
}

async function fetchObservations(deploymentIds: string[]): Promise<Observation[]> {
  const { data, error } = await fetchLiveObservations<Observation>(supabase, {
    columns: 'id, deployment_id, scientific_name, observation_type, created_at',
    filter: q => q.in('deployment_id', deploymentIds),
  })
  if (error) throw new Error(error.message)
  return data || []
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

  const [selectedDepId, setSelectedDepId] = useState<string | null>(null)
  const [selectedDeps, setSelectedDeps] = useState<Set<string>>(new Set())
  const [mapMetric, setMapMetric] = useState<MapMetric>('total')
  const [mapShowAbsent, setMapShowAbsent] = useState(true)

  const [reportFilterSpecies, setReportFilterSpecies] = useState('')
  const [mapFilterSpecies, setMapFilterSpecies]       = useState('')

  // The Reports ▸ Deployment filter lives in the URL, so the view is
  // shareable/bookmarkable, arrivals via ?deployment= land pre-focused, and
  // back/forward moves it.
  const reportFilterDep = deploymentParam
  const setReportFilterDep = (id: string) => {
    const next = new URLSearchParams(searchParams)
    if (id) next.set('deployment', id)
    else next.delete('deployment')
    setSearchParams(next, { replace: true })
  }

  // With nothing selected the page shows only a deep-linked ?deployment=, or the empty state.
  const linkOnly = noProjectSelected && !!deploymentParam
  const showNothing = noProjectSelected && !deploymentParam

  // Load deployments (both tabs use them) ──────────────────────────────────
  const queryClient = useQueryClient()
  const depEnabled = !!user && (!!queryProjectIds || linkOnly)
  const depQuery = useQuery({
    queryKey: ['insights', 'deployments', user?.id, queryProjectIds, deploymentParam],
    queryFn: () => fetchDeployments(queryProjectIds, deploymentParam),
    enabled: depEnabled,
    // A new selection keeps the last list until its own arrives, so the reports hold their
    // data instead of flashing empty; the deployments tab shows its loading state meanwhile.
    placeholderData: keepPreviousData,
  })
  const deployments = depQuery.data ?? NO_DEPLOYMENTS
  const depLoading = depEnabled && (depQuery.isPending || depQuery.isPlaceholderData)
  const error = depQuery.error?.message ?? null
  // After a deployment delete/undo, location edit or move.
  const refreshDeployments = () => {
    void queryClient.invalidateQueries({ queryKey: ['insights', 'deployments'] })
  }

  // Load observations (reports always; map view when shown) ─────────────────
  const depIds = useMemo(() => deployments.map(d => d.id), [deployments])
  const obsEnabled = !!user && !showNothing && (tab === 'reports' || tab === 'map') && depIds.length > 0
  const obsQuery = useQuery({
    queryKey: ['insights', 'observations', user?.id, depIds],
    queryFn: () => fetchObservations(depIds),
    enabled: obsEnabled,
    // While an upload is processing, refetch periodically so partial AI results stream into
    // the reports/map without a manual page refresh. A refetch keeps the data on screen, so
    // it never flashes "Loading…" over live data.
    refetchInterval: uploadActive ? 8000 : false,
  })
  const observations = obsQuery.data ?? NO_OBSERVATIONS
  const obsLoading = obsEnabled && obsQuery.isPending
  // Each upload phase change (notably the end of classification) reads the results once more.
  const lastUploadPhase = useRef(uploadPhase)
  useEffect(() => {
    if (lastUploadPhase.current === uploadPhase) return
    lastUploadPhase.current = uploadPhase
    void queryClient.invalidateQueries({ queryKey: ['insights', 'observations'] })
  }, [uploadPhase, queryClient])

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
              onDeleted={() => { setSelectedDeps(new Set()); refreshDeployments() }}
              onEdited={refreshDeployments}
              onMoved={() => { setSelectedDeps(new Set()); refreshDeployments() }}
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
