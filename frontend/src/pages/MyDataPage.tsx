import { useState, useEffect, useMemo, useRef } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../config/supabase'
import { fetchLiveObservations } from '../lib/liveObservations'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { DeploymentMap } from '../components/data/DeploymentMap'
import { ObservationReports } from '../components/data/ObservationReports'
import { MediaBrowser } from '../components/data/MediaBrowser'
import { NoProjectSelected } from '../components/common/NoProjectSelected'
import { CamtrapExportStatus } from '../components/data/CamtrapExportStatus'
import { useCamtrapExport } from '../hooks/useCamtrapExport'
import { useClusters } from '../hooks/useBrain'

// ─────────────────────────────────────────────────────────────────────────────
// Shared button style (lifecycle nav + overflow)
// ─────────────────────────────────────────────────────────────────────────────

const NAV_BTN: React.CSSProperties = {
  padding: '0.25rem 0.5rem',
  fontSize: '0.75rem',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  backgroundColor: 'transparent',
  color: 'var(--primary)',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
}

// ─────────────────────────────────────────────────────────────────────────────
// OverflowMenu — secondary actions hidden behind a "⋯" button
// ─────────────────────────────────────────────────────────────────────────────

interface OverflowItem { label: string; onClick: () => void; title?: string }

function OverflowMenu({ items }: { items: OverflowItem[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={e => { e.stopPropagation(); setOpen(v => !v) }}
        style={{ ...NAV_BTN, letterSpacing: '0.05em' }}
        title="More actions"
      >
        ⋯
      </button>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: '110%', zIndex: 40,
          backgroundColor: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
          minWidth: '140px', padding: '0.25rem 0',
        }}>
          {items.map(item => (
            <button
              key={item.label}
              title={item.title}
              onClick={e => { e.stopPropagation(); setOpen(false); item.onClick() }}
              style={{
                display: 'block', width: '100%', textAlign: 'left',
                padding: '0.4rem 0.75rem', fontSize: '0.8125rem',
                border: 'none', backgroundColor: 'transparent',
                color: 'var(--text-color)', cursor: 'pointer',
              }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.08)')}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// DeploymentActionRow — v4 lifecycle nav with live cluster badge
// Each row is its own component so useClusters can be called at the top level.
// ─────────────────────────────────────────────────────────────────────────────

interface Deployment {
  id: string
  project_id: string
  project_name?: string
  device_name?: string
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  created_at: string
  timezone?: string | null
  observation_count?: number
}

function DeploymentActionRow({ d, navigate }: { d: Deployment; navigate: ReturnType<typeof useNavigate> }) {
  const { data: brainData } = useClusters(d.id)

  const clusters      = brainData?.clusters ?? []
  const total         = clusters.length
  const confirmed     = clusters.filter(c => c.review_state === 'confirmed').length
  const openCount     = clusters.filter(c => c.review_state === 'open').length
  const hasRun        = !!brainData?.embedding_run_id

  return (
    <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap', alignItems: 'center' }}>
      {/* Primary lifecycle steps */}
      <button style={NAV_BTN} title="Browse, cluster and label images in this deployment"
        onClick={e => { e.stopPropagation(); navigate(`/annotations?deployment=${d.id}`) }}>
        🖼 Images
      </button>

      <button
        style={{ ...NAV_BTN, ...(hasRun && confirmed < total ? { fontWeight: 600 } : {}) }}
        title={hasRun ? `${confirmed} of ${total} clusters confirmed` : 'Run Wildlife Brain to generate clusters'}
        onClick={e => { e.stopPropagation(); navigate(`/clusters/${d.id}`) }}
      >
        ◧ Clusters{hasRun ? ` (${confirmed}/${total})` : ''}
      </button>

      <button
        style={{ ...NAV_BTN, ...(openCount > 0 ? { color: 'var(--warning, #f59e0b)', borderColor: 'var(--warning, #f59e0b)' } : {}) }}
        title={openCount > 0 ? `${openCount} items pending review` : 'Active-learning review queue'}
        onClick={e => { e.stopPropagation(); navigate(`/review/${d.id}`) }}
      >
        ▶ Review{openCount > 0 ? ` (${openCount})` : ''}
      </button>

      <button style={NAV_BTN} title="Visualise embedding space"
        onClick={e => { e.stopPropagation(); navigate(`/umap/${d.id}`) }}>
        ✦ UMAP
      </button>

      {/* Secondary actions in overflow */}
      <OverflowMenu items={[
        { label: '📊 Results', title: 'Species diversity, activity & exports', onClick: () => navigate(`/reporting/${d.id}`) },
      ]} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
}

// Deployment is declared above (needed by DeploymentActionRow)

interface Observation {
  id: string
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  created_at: string
}

const NO_PROJECTS: Project[] = []
const NO_DEPLOYMENTS: Deployment[] = []
const NO_OBSERVATIONS: Observation[] = []

async function fetchProjects(): Promise<Project[]> {
  const { data, error } = await supabase
    .from('projects')
    .select('id, name, description, created_at')
    .is('deleted_at', null)
    .order('created_at', { ascending: false })
  if (error) throw new Error(error.message)
  return data || []
}

async function fetchDeployments(projectIds: string[]): Promise<Deployment[]> {
  // `timezone` may not be deployed yet → retry without it so the page still loads.
  const baseCols = 'id, project_id, location_name, latitude, longitude, deployment_start, deployment_end, created_at, projects(name), devices(name)'
  const runQuery = (cols: string) => supabase
    .from('deployments')
    .select(cols)
    .is('deleted_at', null)
    .order('created_at', { ascending: false })
    .in('project_id', projectIds)

  let { data, error } = await runQuery(`${baseCols}, timezone`)
  if (error) ({ data, error } = await runQuery(baseCols))
  if (error) throw new Error(error.message)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data || []).map((d: any) => ({
    ...d,
    project_name: d.projects?.name ?? '—',
    device_name: d.devices?.name ?? '—',
    projects: undefined,
    devices: undefined,
  })) as Deployment[]
}

async function fetchObservations(deploymentIds: string[]): Promise<Observation[]> {
  const { data, error } = await fetchLiveObservations<Observation>(supabase, {
    columns: 'id, deployment_id, scientific_name, observation_type, created_at',
    filter: q => q.in('deployment_id', deploymentIds),
  })
  if (error) throw new Error(error.message)
  return data || []
}

type Tab = 'projects' | 'deployments' | 'map' | 'reports' | 'media'

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function MyDataPage() {
  const { user } = useAuth()
  const { selectedProjectIds, queryProjectIds, noProjectSelected, clearAll, toggleProject } = useProjectSelection()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('projects')
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null)
  const [sortCol, setSortCol] = useState<string>('')
  const [sortAsc, setSortAsc] = useState(true)
  const [search, setSearch] = useState('')
  const camtrapExport = useCamtrapExport()

  // ── Fetch projects ──────────────────────────────────────────────────────────
  const projectsQuery = useQuery({
    queryKey: ['my-data', 'projects', user?.id],
    queryFn: fetchProjects,
    enabled: !!user,
  })
  const projects = projectsQuery.data ?? NO_PROJECTS

  // ── Fetch deployments (with observation counts) ─────────────────────────────
  const depEnabled = !!user && tab !== 'projects' && !!queryProjectIds
  const depQuery = useQuery({
    queryKey: ['my-data', 'deployments', user?.id, queryProjectIds],
    queryFn: () => fetchDeployments(queryProjectIds ?? []),
    enabled: depEnabled,
    // A new selection keeps the last list (shown as loading) until its own arrives.
    placeholderData: keepPreviousData,
  })
  const deployments = depQuery.data ?? NO_DEPLOYMENTS

  // The projects tab reads the project list, every other tab the deployments.
  const loading = tab === 'projects'
    ? !!user && projectsQuery.isPending
    : depEnabled && (depQuery.isPending || depQuery.isPlaceholderData)
  const error = (tab === 'projects' ? projectsQuery.error : depQuery.error)?.message ?? null

  // ── Fetch observations for Map + Reports tabs ───────────────────────────────
  const depIds = useMemo(() => deployments.map(d => d.id), [deployments])
  const obsEnabled = !!user && (tab === 'map' || tab === 'reports') && !noProjectSelected && depIds.length > 0
  const obsQuery = useQuery({
    queryKey: ['my-data', 'observations', user?.id, depIds],
    queryFn: () => fetchObservations(depIds),
    enabled: obsEnabled,
  })
  const observations = obsQuery.data ?? NO_OBSERVATIONS
  const obsLoading = obsEnabled && obsQuery.isPending

  // The deployment tabs show nothing, and query nothing, while no project is selected.
  const showNothing = noProjectSelected && tab !== 'projects'

  // Enrich deployments with observation counts
  const deploymentsWithCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const o of observations) counts[o.deployment_id] = (counts[o.deployment_id] ?? 0) + 1
    return deployments.map(d => ({ ...d, observation_count: counts[d.id] ?? 0 }))
  }, [deployments, observations])

  // ── Sorting / filtering ─────────────────────────────────────────────────────
  const handleSort = (col: string) => {
    if (sortCol === col) setSortAsc(!sortAsc)
    else { setSortCol(col); setSortAsc(true) }
  }

  const sortedProjects = useMemo(() => {
    const filtered = projects.filter(p => !search || p.name.toLowerCase().includes(search.toLowerCase()))
    if (sortCol) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      filtered.sort((a: any, b: any) => {
        const va = a[sortCol] ?? ''; const vb = b[sortCol] ?? ''
        return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
      })
    }
    return filtered
  }, [projects, sortCol, sortAsc, search])

  const sortedDeployments = useMemo(() => {
    const filtered = deployments.filter(d =>
      !search ||
      (d.location_name || '').toLowerCase().includes(search.toLowerCase()) ||
      (d.project_name || '').toLowerCase().includes(search.toLowerCase()) ||
      (d.device_name || '').toLowerCase().includes(search.toLowerCase())
    )
    if (sortCol) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      filtered.sort((a: any, b: any) => {
        const va = a[sortCol] ?? ''; const vb = b[sortCol] ?? ''
        return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
      })
    }
    return filtered
  }, [deployments, sortCol, sortAsc, search])

  // ── CSV download ────────────────────────────────────────────────────────────
  const downloadCsv = (filename: string, headers: string[], rows: (string | number | null)[][]) => {
    const csv = [headers.join(','), ...rows.map(r => r.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = filename; a.click()
    URL.revokeObjectURL(url)
  }

  const exportProjectsCsv = () => downloadCsv('projects.csv',
    ['ID', 'Name', 'Description', 'Created'],
    sortedProjects.map(p => [p.id, p.name, p.description || '', p.created_at])
  )
  const exportDeploymentsCsv = () => downloadCsv('deployments.csv',
    ['ID', 'Project', 'Device', 'Location', 'Latitude', 'Longitude', 'Start', 'End', 'Created'],
    sortedDeployments.map(d => [d.id, d.project_name || '', d.device_name || '', d.location_name || '', d.latitude || '', d.longitude || '', d.deployment_start || '', d.deployment_end || '', d.created_at])
  )

  // ── Style helpers ────────────────────────────────────────────────────────────
  const renderSortIcon = (col: string) => (
    <span style={{ opacity: sortCol === col ? 1 : 0.3, marginLeft: '4px', fontSize: '0.75rem' }}>
      {sortCol === col ? (sortAsc ? '▲' : '▼') : '⇅'}
    </span>
  )
  const thStyle: React.CSSProperties = {
    padding: '0.625rem 0.5rem', textAlign: 'left', cursor: 'pointer',
    userSelect: 'none', whiteSpace: 'nowrap', borderBottom: '2px solid var(--border)',
    fontSize: '0.8125rem', fontWeight: 600,
  }
  const tdStyle: React.CSSProperties = {
    padding: '0.5rem', borderBottom: '1px solid var(--border)', fontSize: '0.8125rem',
  }

  const TABS: { id: Tab; label: string }[] = [
    { id: 'projects', label: '📂 Projects' },
    { id: 'deployments', label: '📍 Deployments' },
    { id: 'map', label: '🗺 Map' },
    { id: 'reports', label: '📊 Reports' },
    { id: 'media', label: '📷 Media' },
  ]

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div>
      <h2 style={{ marginBottom: '0.5rem' }}>My Wildlife Watcher Data</h2>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Browse projects and deployments, explore observation maps and reports, download CamtrapDP packages, or import data from other tools.
      </p>



      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '2px solid var(--border)', marginBottom: '1.5rem' }}>
        {TABS.map(t => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            onClick={() => { setTab(t.id); setSearch(''); setSortCol('') }}
            style={{
              padding: '0.625rem 1.25rem', border: 'none',
              borderBottom: tab === t.id ? '2px solid var(--primary)' : '2px solid transparent',
              backgroundColor: 'transparent',
              color: tab === t.id ? 'var(--primary)' : 'var(--text-color)',
              fontWeight: tab === t.id ? 600 : 400,
              cursor: 'pointer', marginBottom: '-2px',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Project filter (shared across non-projects tabs) */}
      {tab !== 'projects' && (
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
          {tab === 'deployments' && (
            <>
              <input
                type="text" placeholder="Search…" value={search}
                onChange={e => setSearch(e.target.value)}
                style={{ flex: 1, minWidth: '200px', padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)' }}
              />
              <button className="btn" onClick={exportDeploymentsCsv} style={{ padding: '0.5rem 1rem', whiteSpace: 'nowrap' }}>
                ⬇ CSV
              </button>
              <button
                id="download-camtrapdp-btn"
                className="btn"
                onClick={() => camtrapExport.start(selectedProjectIds)}
                disabled={camtrapExport.running || selectedProjectIds.length !== 1}
                title={selectedProjectIds.length !== 1 ? 'Select exactly one project from the top right to download CamtrapDP' : 'Download CamtrapDP package (ZIP)'}
                style={{ padding: '0.5rem 1rem', whiteSpace: 'nowrap', opacity: selectedProjectIds.length !== 1 ? 0.5 : 1 }}
              >
                {camtrapExport.running ? '⏳ Exporting…' : '📦 Download CamtrapDP'}
              </button>
            </>
          )}
        </div>
      )}

      {tab === 'deployments' && <CamtrapExportStatus state={camtrapExport} style={{ marginBottom: '0.75rem' }} />}

      {error && <p style={{ color: 'var(--error)' }}>{error}</p>}
      {loading && tab !== 'map' && tab !== 'reports' && <p>Loading…</p>}
      {showNothing && <NoProjectSelected />}

      {/* ── Projects tab ─────────────────────────────────────────────────── */}
      {tab === 'projects' && !loading && (
        <>
          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text" placeholder="Search…" value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ flex: 1, minWidth: '200px', padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)' }}
            />
            <button className="btn" onClick={exportProjectsCsv} style={{ padding: '0.5rem 1rem', whiteSpace: 'nowrap' }}>
              ⬇ Download CSV
            </button>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle} onClick={() => handleSort('name')}>Name {renderSortIcon('name')}</th>
                  <th style={thStyle} onClick={() => handleSort('description')}>Description {renderSortIcon('description')}</th>
                  <th style={thStyle} onClick={() => handleSort('created_at')}>Created {renderSortIcon('created_at')}</th>
                  <th style={{ ...thStyle, cursor: 'default' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedProjects.length === 0 && (
                  <tr><td colSpan={4} style={{ ...tdStyle, textAlign: 'center', opacity: 0.5, padding: '2rem' }}>No projects found</td></tr>
                )}
                {sortedProjects.map(p => (
                  <tr key={p.id}
                    onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.04)')}
                    onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                    style={{ transition: 'background-color 0.15s' }}
                  >
                    <td style={{ ...tdStyle, fontWeight: 500 }}>{p.name}</td>
                    <td style={{ ...tdStyle, opacity: 0.7 }}>{p.description || '—'}</td>
                    <td style={{ ...tdStyle, fontSize: '0.75rem' }}>{new Date(p.created_at).toLocaleDateString()}</td>
                    <td style={tdStyle}>
                      <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
                        <button onClick={() => { clearAll(); toggleProject(p.id); setTab('deployments') }}
                          style={NAV_BTN}>
                          📍 Deployments
                        </button>
                        <button onClick={() => { clearAll(); toggleProject(p.id); setTab('map') }}
                          style={NAV_BTN}>
                          🗺 Map
                        </button>
                        <button onClick={() => navigate(`/intelligence/${p.id}`)}
                          style={NAV_BTN}
                          title="Dataset health dashboard">
                          📊 Health
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── Deployments tab ──────────────────────────────────────────────── */}
      {tab === 'deployments' && !loading && !showNothing && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={thStyle} onClick={() => handleSort('project_name')}>Project {renderSortIcon('project_name')}</th>
                <th style={thStyle} onClick={() => handleSort('device_name')}>Device {renderSortIcon('device_name')}</th>
                <th style={thStyle} onClick={() => handleSort('location_name')}>Location {renderSortIcon('location_name')}</th>
                <th style={thStyle} onClick={() => handleSort('latitude')}>GPS {renderSortIcon('latitude')}</th>
                <th style={thStyle} onClick={() => handleSort('deployment_start')}>Start {renderSortIcon('deployment_start')}</th>
                <th style={thStyle} onClick={() => handleSort('deployment_end')}>End {renderSortIcon('deployment_end')}</th>
                <th style={{ ...thStyle, cursor: 'default' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedDeployments.length === 0 && (
                <tr><td colSpan={7} style={{ ...tdStyle, textAlign: 'center', opacity: 0.5, padding: '2rem' }}>No deployments found</td></tr>
              )}
              {sortedDeployments.map(d => (
                <tr key={d.id}
                  style={{ transition: 'background-color 0.15s', cursor: 'pointer' }}
                  onClick={() => setSelectedDeploymentId(d.id)}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.04)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <td style={{ ...tdStyle, fontWeight: 500 }}>{d.project_name}</td>
                  <td style={tdStyle}>{d.device_name}</td>
                  <td style={tdStyle}>{d.location_name || '—'}</td>
                  <td style={{ ...tdStyle, fontSize: '0.75rem', fontFamily: 'monospace' }}>
                    {d.latitude && d.longitude ? `${Number(d.latitude).toFixed(4)}, ${Number(d.longitude).toFixed(4)}` : '—'}
                  </td>
                  <td style={{ ...tdStyle, fontSize: '0.75rem' }}>{d.deployment_start ? new Date(d.deployment_start).toLocaleDateString() : '—'}</td>
                  <td style={{ ...tdStyle, fontSize: '0.75rem' }}>{d.deployment_end ? new Date(d.deployment_end).toLocaleDateString() : '—'}</td>
                  <td style={tdStyle} onClick={e => e.stopPropagation()}>
                    <DeploymentActionRow d={d} navigate={navigate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Map tab ──────────────────────────────────────────────────────── */}
      {tab === 'map' && !showNothing && (
        <>
          {loading ? <p>Loading deployments…</p> : (
            <DeploymentMap
              deployments={deploymentsWithCounts}
              selectedDeploymentId={selectedDeploymentId}
              onSelectDeployment={setSelectedDeploymentId}
            />
          )}
        </>
      )}

      {/* ── Reports tab ──────────────────────────────────────────────────── */}
      {tab === 'reports' && !showNothing && (
        <ObservationReports
          observations={observations}
          deployments={deployments}
          loading={obsLoading}
        />
      )}

      {/* ── Media tab ──────────────────────────────────────────────────────── */}
      {tab === 'media' && !showNothing && (
        <MediaBrowser
          deployments={deployments.map(d => ({ id: d.id, location_name: d.location_name, project_id: d.project_id, timezone: d.timezone, deployment_start: d.deployment_start }))}
        />
      )}
    </div>
  )
}
