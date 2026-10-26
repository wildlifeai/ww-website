// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * InsightsPage: /insights
 *
 * Three sub-tabs via ?tab=reports|deployments|map (default reports).
 * Reports: fixed counters and charts (ReportsDashboard), filtered by deployment (?deployment=,
 * repeatable, so a link from an upload lands focused) and species. With one deployment in view
 * the per-deployment chart becomes detections per day by species.
 * Deployments: the table, with row selection and one Actions menu (DeploymentBulkActions).
 * Map: species pie markers, or present/absent sites for the chosen species, and the deployments
 * without GPS listed under it.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Camera, MapPin } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useProjectRoles } from '../hooks/useProjectRoles'
import { useFilledTimezones } from '../hooks/useFilledTimezones'
import { useDeploymentFilterOptions } from '../hooks/useDeploymentFilterOptions'
import { supabase } from '../config/supabase'
import { startDateLabel } from '../lib/deploymentFilterOptions'
import {
  fetchDailyCounts, fetchInsightsDeployments, fetchObservationCounts, type InsightsDeployment,
} from '../lib/insightsReads'
import { filterCounts, type ObservationCount } from '../lib/observationSummary'
import { DataTable, type Column } from '../components/ui/DataTable'
import { MultiSelect } from '../components/ui/MultiSelect'
import { Ribbon, type RibbonGroupDef } from '../components/ui/Ribbon'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { ReportsDashboard, type SingleDeployment } from '../components/data/ReportsDashboard'
import { LiveInsightsBanner } from '../components/data/LiveInsightsBanner'
import { DeploymentBulkActions } from '../components/data/DeploymentBulkActions'
import { EditLocationDialog } from '../components/data/EditLocationDialog'
import { useUploadStore } from '../contexts/UploadContext'
import { NoProjectSelected } from '../components/common/NoProjectSelected'

type InsightsTab = 'reports' | 'deployments' | 'map'
type MapMetric = 'total' | 'perDay'

const TABS: { id: InsightsTab; label: string }[] = [
  { id: 'reports',     label: 'Reports' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'map',         label: 'Map' },
]

const UUID = /^[0-9a-fA-F-]{36}$/
const MUTED: React.CSSProperties = { color: 'var(--text-muted)', fontSize: '0.8125rem' }
const DASH = <span style={{ color: 'var(--text-muted)' }}>-</span>

// Active span of a deployment in days (min 1). Open-ended deployments run up to
// "now". Module-level so the date maths stays out of render purity checks.
function activeDaysOf(start: string | null, end: string | null): number {
  if (!start) return 1
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  return Math.max(1, Math.round((e - s) / 86_400_000))
}

/**
 * Map markers: per-deployment detection count (optionally for the chosen species),
 * effort-normalised to a per-active-day rate, and present/absent flags.
 */
function buildMapMarkers(
  deployments: InsightsDeployment[], observationCounts: ObservationCount[],
  speciesFilter: string[], metric: MapMetric, showAbsent: boolean,
) {
  const species = new Set(speciesFilter)
  const counts: Record<string, number> = {}
  // Per-deployment species tally: drives the default pie-chart markers.
  const speciesByDep: Record<string, Record<string, number>> = {}
  for (const o of observationCounts) {
    if (species.size > 0 && !species.has(o.scientific_name ?? '')) continue
    counts[o.deployment_id] = (counts[o.deployment_id] ?? 0) + o.n
    const sp = o.scientific_name
    if (sp && sp !== '(unidentified)') {
      const dep = (speciesByDep[o.deployment_id] ??= {})
      dep[sp] = (dep[sp] ?? 0) + o.n
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
      metricValue: metric === 'perDay' ? perDay : count,
      speciesCounts: speciesByDep[d.id] ?? {},
    }
  })
  // When a species is chosen, optionally drop the "absent" sites.
  if (species.size > 0 && !showAbsent) return markers.filter(m => m.present)
  return markers
}

/** The deployments the map cannot place, each with Edit location for those who may change it. */
function WithoutGpsList({ deployments, labels, onEdited }: {
  deployments: InsightsDeployment[]
  labels: ReadonlyMap<string, string>
  onEdited: () => void
}) {
  const roles = useProjectRoles()
  const [editingId, setEditingId] = useState<string | null>(null)
  if (deployments.length === 0) return null
  return (
    <>
      <details style={{ marginTop: '0.75rem', fontSize: '0.8125rem' }}>
        <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>{deployments.length} without GPS</summary>
        <ul style={{ listStyle: 'none', padding: 0, margin: '0.5rem 0 0', maxHeight: 240, overflowY: 'auto' }}>
          {deployments.map(d => (
            <li key={d.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.3rem 0', borderTop: '1px solid var(--border)' }}>
              <span>{labels.get(d.id) ?? d.id.slice(0, 8)}</span>
              {!roles.loading && roles.canWrite(d.project_id) && (
                <button onClick={() => setEditingId(d.id)} style={LINK_BTN}>
                  <MapPin size={12} aria-hidden />Edit location
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>
      {editingId && (
        <EditLocationDialog
          deploymentId={editingId}
          onClose={changed => { setEditingId(null); if (changed) onEdited() }}
        />
      )}
    </>
  )
}

const NO_DEPLOYMENTS: InsightsDeployment[] = []
const NO_COUNTS: ObservationCount[] = []

const VIEW_BTN = (active: boolean): React.CSSProperties => ({
  padding: '0.3rem 0.7rem', fontSize: '0.78rem', cursor: 'pointer', whiteSpace: 'nowrap',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: active ? 'var(--primary)' : 'transparent',
  color: active ? '#fff' : 'var(--text-color)', fontWeight: active ? 600 : 400,
})

const LINK_BTN: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.25rem', marginLeft: 'auto', padding: 0,
  border: 'none', background: 'none', color: 'var(--primary)', cursor: 'pointer', fontSize: '0.8125rem',
}

const deploymentColumns: Column<InsightsDeployment>[] = [
  { key: 'project_name', label: 'Project', cellStyle: { fontWeight: 500 } },
  { key: 'device_name',  label: 'Device' },
  {
    key: 'location_name', label: 'Location',
    render: r => r.location_name || DASH,
    getValue: r => r.location_name ?? '',
  },
  {
    key: 'gps', label: 'GPS', sortable: false,
    cellStyle: { fontFamily: 'monospace', fontSize: '0.75rem' },
    render: r => r.latitude != null && r.longitude != null
      ? `${Number(r.latitude).toFixed(4)}, ${Number(r.longitude).toFixed(4)}`
      : DASH,
    // Full precision in the CSV.
    getValue: r => (r.latitude != null && r.longitude != null ? `${r.latitude}, ${r.longitude}` : ''),
  },
  // Dates in the deployment's own zone, as Annotations labels them (UI audit 04 #20).
  { key: 'deployment_start', label: 'Start', cellStyle: { fontSize: '0.75rem' }, render: r => startDateLabel(r.deployment_start, r.timezone) ?? DASH },
  { key: 'deployment_end',   label: 'End',   cellStyle: { fontSize: '0.75rem' }, render: r => startDateLabel(r.deployment_end, r.timezone) ?? DASH },
]

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

  // The Reports deployment filter lives in the URL (?deployment=<uuid>, repeatable), so a view
  // is shareable, an arrival from an upload lands focused, and back/forward moves it. Each id
  // is UUID-guarded before it reaches a query filter.
  const deploymentParamKey = searchParams.getAll('deployment').filter(d => UUID.test(d)).join(',')
  const reportFilterDeps = useMemo(() => (deploymentParamKey ? deploymentParamKey.split(',') : []), [deploymentParamKey])
  const setReportFilterDeps = (ids: string[]) => {
    const next = new URLSearchParams(searchParams)
    next.delete('deployment')
    ids.forEach(id => next.append('deployment', id))
    setSearchParams(next, { replace: true })
  }

  const [selectedDepId, setSelectedDepId] = useState<string | null>(null)
  const [selectedDeps, setSelectedDeps] = useState<Set<string>>(new Set())
  const [mapMetric, setMapMetric] = useState<MapMetric>('total')
  const [mapShowAbsent, setMapShowAbsent] = useState(true)
  const [reportFilterSpecies, setReportFilterSpecies] = useState<string[]>([])
  const [mapFilterSpecies, setMapFilterSpecies] = useState<string[]>([])

  // With nothing selected the page shows only deep-linked deployments, or the empty state.
  const linkOnly = noProjectSelected && reportFilterDeps.length > 0
  const showNothing = noProjectSelected && reportFilterDeps.length === 0

  // Load deployments (every tab uses them) ─────────────────────────────────
  const queryClient = useQueryClient()
  const depEnabled = !!user && (!!queryProjectIds || linkOnly)
  const depQuery = useQuery({
    queryKey: ['insights', 'deployments', user?.id, queryProjectIds, reportFilterDeps],
    queryFn: () => fetchInsightsDeployments(supabase, queryProjectIds, reportFilterDeps),
    enabled: depEnabled,
    // A new selection keeps the last list until its own arrives, so the reports hold their
    // data instead of flashing empty; the deployments tab shows its loading state meanwhile.
    placeholderData: keepPreviousData,
  })
  const loadedDeployments = depQuery.data ?? NO_DEPLOYMENTS
  // Zones the backend fills for deployments created without one (#309), as Annotations does.
  const deployments = useFilledTimezones(loadedDeployments)
  const depLoading = depEnabled && (depQuery.isPending || depQuery.isPlaceholderData)
  const error = depQuery.error?.message ?? null
  // After a deployment delete/undo, location edit or move.
  const refreshDeployments = () => {
    void queryClient.invalidateQueries({ queryKey: ['insights', 'deployments'] })
  }

  // Load the observation counts (reports and map) ──────────────────────────
  // The database counts them (ww-backend #334): one request over the deployments' projects, and
  // over the deep-linked deployments alone when no project is selected (lib/insightsReads.ts).
  const depIds = useMemo(() => loadedDeployments.map(d => d.id), [loadedDeployments])
  const narrowCounts = !queryProjectIds
  const countsEnabled = !!user && !showNothing && (tab === 'reports' || tab === 'map') && depIds.length > 0
  // While an upload is processing, the counts are read again periodically so partial AI results
  // stream into the reports/map without a manual page refresh. A refetch keeps the data on
  // screen, so it never flashes "Loading…" over live data.
  const refetchInterval = uploadActive ? 8000 : false
  const countsQuery = useQuery({
    queryKey: ['insights', 'observations', 'counts', user?.id, depIds, narrowCounts],
    queryFn: () => fetchObservationCounts(supabase, loadedDeployments, narrowCounts),
    enabled: countsEnabled,
    refetchInterval,
  })
  const counts = countsQuery.data ?? NO_COUNTS
  const countsLoading = countsEnabled && countsQuery.isPending
  // Each upload phase change (notably the end of classification) reads the results once more.
  const lastUploadPhase = useRef(uploadPhase)
  useEffect(() => {
    if (lastUploadPhase.current === uploadPhase) return
    lastUploadPhase.current = uploadPhase
    void queryClient.invalidateQueries({ queryKey: ['insights', 'observations'] })
  }, [uploadPhase, queryClient])

  const mapMarkers = useMemo(
    () => buildMapMarkers(deployments, counts, mapFilterSpecies, mapMetric, mapShowAbsent),
    [deployments, counts, mapFilterSpecies, mapMetric, mapShowAbsent],
  )

  const withoutGps = useMemo(() => deployments.filter(d => d.latitude == null || d.longitude == null), [deployments])

  // The filters apply to the counted rows, so changing one reads nothing.
  const filteredCounts = useMemo(
    () => filterCounts(counts, reportFilterDeps, reportFilterSpecies),
    [counts, reportFilterDeps, reportFilterSpecies],
  )

  const speciesOptions = useMemo(() => {
    const names = new Set<string>()
    counts.forEach(o => { if (o.scientific_name) names.add(o.scientific_name) })
    return Array.from(names).sort().map(n => ({ value: n, label: n }))
  }, [counts])

  // Location, start date and (across projects) project name, so re-deployed sites read apart.
  const deploymentOptions = useDeploymentFilterOptions(deployments)
  const deploymentLabels = useMemo(() => new Map(deploymentOptions.map(o => [o.value, o.label])), [deploymentOptions])

  // One deployment in view (filtered to one, or only one exists): the per-day chart, dated in
  // its zone, and its period and photos when it has no animal observations.
  const singleDeployment = useMemo<SingleDeployment | null>(() => {
    const ids = reportFilterDeps.length > 0 ? reportFilterDeps : deployments.map(d => d.id)
    if (ids.length !== 1) return null
    const d = deployments.find(x => x.id === ids[0])
    return d ? { id: d.id, timezone: d.timezone, deployment_start: d.deployment_start, deployment_end: d.deployment_end } : null
  }, [reportFilterDeps, deployments])

  // Its detections per capture day, dated by the database in the zone the page shows it in, so
  // a zone filled on first view (#309) reads them again.
  const singleId = singleDeployment?.id ?? null
  const singleZone = singleDeployment?.timezone ?? null
  const dailyEnabled = !!user && !showNothing && tab === 'reports' && !!singleId
  const dailyQuery = useQuery({
    queryKey: ['insights', 'observations', 'daily', user?.id, singleId, singleZone],
    queryFn: () => fetchDailyCounts(supabase, singleId as string, singleZone),
    enabled: dailyEnabled,
    refetchInterval,
  })
  const dailyCounts = useMemo(
    () => (dailyQuery.data ? filterCounts(dailyQuery.data, [], reportFilterSpecies) : undefined),
    [dailyQuery.data, reportFilterSpecies],
  )

  const ribbonGroupsFor = (id: InsightsTab): RibbonGroupDef[] => {
    if (id === 'reports') {
      return [
        { id: 'deployment', title: 'Deployment', content: (
          <MultiSelect values={reportFilterDeps} onChange={setReportFilterDeps} options={deploymentOptions}
            allLabel="All deployments" noun="deployment" searchable />
        ) },
        { id: 'species', title: 'Species', content: (
          <MultiSelect values={reportFilterSpecies} onChange={setReportFilterSpecies} options={speciesOptions}
            allLabel="All species" noun="species" searchable />
        ) },
      ]
    }
    if (id === 'map') {
      const groups: RibbonGroupDef[] = [
        { id: 'species', title: 'Species', content: (
          <MultiSelect values={mapFilterSpecies} onChange={setMapFilterSpecies} options={speciesOptions}
            allLabel="All species" noun="species" searchable />
        ) },
        { id: 'metric', title: 'Size by', content: (
          <div style={{ display: 'flex', gap: '0.375rem' }}>
            <button style={VIEW_BTN(mapMetric === 'total')}  onClick={() => setMapMetric('total')}>Total</button>
            <button style={VIEW_BTN(mapMetric === 'perDay')} onClick={() => setMapMetric('perDay')}>Per day</button>
          </div>
        ) },
      ]
      if (mapFilterSpecies.length > 0) {
        groups.push({ id: 'absent', title: 'Absent sites', content: (
          <div style={{ display: 'flex', gap: '0.375rem' }}>
            <button style={VIEW_BTN(mapShowAbsent)}  onClick={() => setMapShowAbsent(true)}>Show</button>
            <button style={VIEW_BTN(!mapShowAbsent)} onClick={() => setMapShowAbsent(false)}>Hide</button>
          </div>
        ) })
      }
      return groups
    }
    // Deployments: the table carries its own search and export; the count sits in the tab bar.
    return []
  }

  if (showNothing) return <NoProjectSelected />

  const reviewSpecies = reportFilterSpecies.length === 1 ? reportFilterSpecies[0] : null
  const mapSpeciesLabel = mapFilterSpecies.length === 0
    ? null
    : mapFilterSpecies.length === 1 ? mapFilterSpecies[0] : `${mapFilterSpecies.length} species`

  return (
    <div>
      {/* Live AI-classification banner: appears while a just-started upload is being analysed. */}
      <LiveInsightsBanner />

      <Ribbon
        activeTabId={tab}
        onTabChange={id => setTab(id as InsightsTab)}
        tabs={TABS.map(t => ({ id: t.id, label: t.label, groups: ribbonGroupsFor(t.id) }))}
        status={tab === 'deployments' && !depLoading
          ? <span style={MUTED}>{deployments.length} deployment{deployments.length !== 1 ? 's' : ''}</span>
          : undefined}
      />

      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error}</p>}

      {/* ── Reports ──────────────────────────────────────────────────── */}
      {tab === 'reports' && (
        <>
          {reviewSpecies && (
            <div style={{ marginBottom: '0.75rem' }}>
              <Link
                to={`/annotations?species=${encodeURIComponent(reviewSpecies)}`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem', fontSize: '0.82rem', color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}
              >
                <Camera size={14} aria-hidden />Review <em>{reviewSpecies}</em> in Media
              </Link>
            </div>
          )}
          <ReportsDashboard
            counts={filteredCounts}
            deploymentLabels={deploymentLabels}
            singleDeployment={singleDeployment}
            dailyCounts={dailyCounts}
            dailyFailed={dailyQuery.isError && !dailyQuery.data}
            speciesFiltered={reportFilterSpecies.length > 0}
            loading={countsLoading}
          />
        </>
      )}

      {/* ── Deployments (table) ──────────────────────────────────────── */}
      {tab === 'deployments' && (
        depLoading ? (
          <p style={MUTED}>Loading deployments…</p>
        ) : (
          <>
            <DeploymentBulkActions
              selected={selectedDeps}
              rows={deployments}
              onClear={() => setSelectedDeps(new Set())}
              onShowMap={() => {
                if (selectedDeps.size === 1) setSelectedDepId([...selectedDeps][0])
                setTab('map')
              }}
              onDeleted={() => { setSelectedDeps(new Set()); refreshDeployments() }}
              onEdited={refreshDeployments}
              onMoved={() => { setSelectedDeps(new Set()); refreshDeployments() }}
            />
            <DataTable<InsightsDeployment>
              columns={deploymentColumns}
              rows={deployments}
              rowKey={r => r.id}
              searchable
              searchPlaceholder="Search deployments…"
              exportFilename="deployments"
              exportJson={false}
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
          <p style={MUTED}>Loading deployments…</p>
        ) : (
          <>
            <DeploymentMap
              deployments={mapMarkers}
              selectedDeploymentId={selectedDepId}
              onSelectDeployment={setSelectedDepId}
              metric={mapMetric}
              speciesLabel={mapSpeciesLabel}
              showSpeciesPie
            />
            <WithoutGpsList deployments={withoutGps} labels={deploymentLabels} onEdited={refreshDeployments} />
          </>
        )
      )}
    </div>
  )
}
