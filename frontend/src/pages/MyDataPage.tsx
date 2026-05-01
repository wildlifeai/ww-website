import { useState, useEffect, useMemo } from 'react'
import { supabase } from '../config/supabase'
import { useAuth } from '../hooks/useAuth'

interface Project {
  id: string
  name: string
  description: string | null
  created_at: string
}

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
}

type Tab = 'projects' | 'deployments'

export function MyDataPage() {
  const { user } = useAuth()
  const [tab, setTab] = useState<Tab>('projects')
  const [projects, setProjects] = useState<Project[]>([])
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<string | null>(null)
  const [sortCol, setSortCol] = useState<string>('')
  const [sortAsc, setSortAsc] = useState(true)
  const [search, setSearch] = useState('')

  // Fetch projects
  useEffect(() => {
    if (!user) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)

    setError(null)

    supabase
      .from('projects')
      .select('id, name, description, created_at')
      .order('created_at', { ascending: false })
      .then(({ data, error: err }) => {
        if (err) setError(err.message)
        else setProjects(data || [])
        setLoading(false)
      })
  }, [user])

  // Fetch deployments (optionally filtered by project)
  useEffect(() => {
    if (!user) return
    if (tab !== 'deployments') return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)

    setError(null)

    let query = supabase
      .from('deployments')
      .select('id, project_id, location_name, latitude, longitude, deployment_start, deployment_end, created_at, projects(name), devices(name)')
      .is('deleted_at', null)
      .order('created_at', { ascending: false })

    if (selectedProject) {
      query = query.eq('project_id', selectedProject)
    }

    query.then(({ data, error: err }) => {
      if (err) setError(err.message)
      else {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const rows = (data || []).map((d: any) => ({
          ...d,
          project_name: d.projects?.name ?? '—',
          device_name: d.devices?.name ?? '—',
          projects: undefined,
          devices: undefined,
        })) as Deployment[]
        setDeployments(rows)
      }
      setLoading(false)
    })
  }, [user, tab, selectedProject])

  // Sorting
  const handleSort = (col: string) => {
    if (sortCol === col) setSortAsc(!sortAsc)
    else { setSortCol(col); setSortAsc(true) }
  }

  const sortedProjects = useMemo(() => {
    const filtered = projects.filter(p =>
      !search || p.name.toLowerCase().includes(search.toLowerCase())
    )
    if (sortCol) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      filtered.sort((a: any, b: any) => {
        const va = a[sortCol] ?? ''
        const vb = b[sortCol] ?? ''
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
        const va = a[sortCol] ?? ''
        const vb = b[sortCol] ?? ''
        return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va))
      })
    }
    return filtered
  }, [deployments, sortCol, sortAsc, search])

  // CSV download
  const downloadCsv = (filename: string, headers: string[], rows: (string | number | null)[][]) => {
    const csv = [headers.join(','), ...rows.map(r => r.map(c => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const exportProjectsCsv = () => {
    downloadCsv('projects.csv',
      ['ID', 'Name', 'Description', 'Created'],
      sortedProjects.map(p => [p.id, p.name, p.description || '', p.created_at])
    )
  }

  const exportDeploymentsCsv = () => {
    downloadCsv('deployments.csv',
      ['ID', 'Project', 'Device', 'Location', 'Latitude', 'Longitude', 'Start', 'End', 'Created'],
      sortedDeployments.map(d => [d.id, d.project_name || '', d.device_name || '', d.location_name || '', d.latitude || '', d.longitude || '', d.deployment_start || '', d.deployment_end || '', d.created_at])
    )
  }

  const renderSortIcon = (col: string) => (
    <span style={{ opacity: sortCol === col ? 1 : 0.3, marginLeft: '4px', fontSize: '0.75rem' }}>
      {sortCol === col ? (sortAsc ? '▲' : '▼') : '⇅'}
    </span>
  )

  const thStyle: React.CSSProperties = {
    padding: '0.625rem 0.5rem',
    textAlign: 'left',
    cursor: 'pointer',
    userSelect: 'none',
    whiteSpace: 'nowrap',
    borderBottom: '2px solid var(--border)',
    fontSize: '0.8125rem',
    fontWeight: 600,
  }

  const tdStyle: React.CSSProperties = {
    padding: '0.5rem',
    borderBottom: '1px solid var(--border)',
    fontSize: '0.8125rem',
  }

  return (
    <div>
      <h2 style={{ marginBottom: '0.5rem' }}>My Wildlife Watcher Data</h2>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Browse the projects and deployments you have access to. Sort columns, search, and export data as CSV.
      </p>

      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '2px solid var(--border)', marginBottom: '1.5rem' }}>
        {(['projects', 'deployments'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => { setTab(t); setSearch(''); setSortCol('') }}
            style={{
              padding: '0.625rem 1.25rem',
              border: 'none',
              borderBottom: tab === t ? '2px solid var(--primary)' : '2px solid transparent',
              backgroundColor: 'transparent',
              color: tab === t ? 'var(--primary)' : 'var(--text-color)',
              fontWeight: tab === t ? 600 : 400,
              cursor: 'pointer',
              marginBottom: '-2px',
              textTransform: 'capitalize',
            }}
          >
            {t === 'projects' ? '📂 Projects' : '📍 Deployments'}
          </button>
        ))}
      </div>

      {/* Toolbar: search + filter + export */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            flex: 1,
            minWidth: '200px',
            padding: '0.5rem 0.75rem',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--surface)',
            color: 'var(--text-color)',
          }}
        />
        {tab === 'deployments' && (
          <select
            value={selectedProject || ''}
            onChange={e => setSelectedProject(e.target.value || null)}
            style={{
              padding: '0.5rem',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--surface)',
              color: 'var(--text-color)',
            }}
          >
            <option value="">All projects</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
        <button
          className="btn"
          onClick={tab === 'projects' ? exportProjectsCsv : exportDeploymentsCsv}
          style={{ padding: '0.5rem 1rem', whiteSpace: 'nowrap' }}
        >
          ⬇ Download CSV
        </button>
      </div>

      {error && <p style={{ color: 'var(--error)' }}>{error}</p>}
      {loading && <p>Loading…</p>}

      {/* Projects table */}
      {!loading && tab === 'projects' && (
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
                <tr key={p.id} style={{ transition: 'background-color 0.15s' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.04)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <td style={{ ...tdStyle, fontWeight: 500 }}>{p.name}</td>
                  <td style={{ ...tdStyle, opacity: 0.7 }}>{p.description || '—'}</td>
                  <td style={{ ...tdStyle, fontSize: '0.75rem' }}>{new Date(p.created_at).toLocaleDateString()}</td>
                  <td style={tdStyle}>
                    <button
                      onClick={() => { setSelectedProject(p.id); setTab('deployments') }}
                      style={{
                        padding: '0.25rem 0.5rem',
                        fontSize: '0.75rem',
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--radius)',
                        backgroundColor: 'transparent',
                        color: 'var(--primary)',
                        cursor: 'pointer',
                      }}
                    >
                      View Deployments →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Deployments table */}
      {!loading && tab === 'deployments' && (
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
              </tr>
            </thead>
            <tbody>
              {sortedDeployments.length === 0 && (
                <tr><td colSpan={6} style={{ ...tdStyle, textAlign: 'center', opacity: 0.5, padding: '2rem' }}>No deployments found</td></tr>
              )}
              {sortedDeployments.map(d => (
                <tr key={d.id}
                  style={{ transition: 'background-color 0.15s' }}
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
