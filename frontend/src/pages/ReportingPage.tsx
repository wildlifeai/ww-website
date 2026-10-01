import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { VegaChart } from '../components/ui/VegaChart'
import { VEGA_CONFIG } from '../lib/vegaSpec'
import { supabase } from '../config/supabase'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ObsRow {
  created_at: string
  scientific_name: string | null
  observation_type: string | null
}

type ChartType = 'bar' | 'line'
type PageTab = 'charts' | 'exports'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function toIso(d: Date) { return d.toISOString().slice(0, 10) }

const COLORS = ['#4caf50', '#2196f3', '#ff9800', '#e91e63', '#9c27b0', '#00bcd4', '#ff5722', '#8bc34a']

/** Build a Vega-Lite spec for the multi-species temporal chart. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildObsSpec(filteredRows: ObsRow[], activeSpecies: string[], chartType: ChartType): Record<string, any> {
  const isBar = chartType === 'bar'
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    height: 320,
    data: { values: filteredRows },
    mark: isBar
      ? { type: 'bar', cornerRadiusTopLeft: 2, cornerRadiusTopRight: 2 }
      : { type: 'line', point: false, strokeWidth: 2 },
    encoding: {
      x: {
        field: 'created_at',
        type: 'temporal',
        timeUnit: 'yearmonthdate',
        title: 'Date',
        axis: { labelAngle: -35, format: '%b %d' },
      },
      y: {
        aggregate: 'count',
        type: 'quantitative',
        title: 'Observations',
      },
      color: {
        field: 'scientific_name',
        type: 'nominal',
        scale: {
          domain: activeSpecies,
          range: activeSpecies.map((_, i) => COLORS[i % COLORS.length]),
        },
        legend: {
          title: null,
          labelFontStyle: 'italic',
          labelFontSize: 11,
          labelColor: '#555',
        },
      },
      tooltip: [
        { field: 'created_at', type: 'temporal', timeUnit: 'yearmonthdate', title: 'Date' },
        { field: 'scientific_name', type: 'nominal', title: 'Species' },
        { aggregate: 'count', title: 'Count' },
      ],
    },
    config: VEGA_CONFIG,
  }
}

/** CSV from raw rows (pivoted into wide format for compatibility). */
function csvFromRows(rows: ObsRow[], activeSpecies: string[]): string {
  // Aggregate by day × species
  const map: Record<string, Record<string, number>> = {}
  for (const r of rows) {
    const day = r.created_at.slice(0, 10)
    const sp = r.scientific_name!
    if (!map[day]) map[day] = {}
    map[day][sp] = (map[day][sp] || 0) + 1
  }
  const header = ['date', ...activeSpecies].join(',')
  const dataRows = Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, counts]) =>
      [date, ...activeSpecies.map((sp) => String(counts[sp] ?? 0))].join(','),
    )
  return [header, ...dataRows].join('\n')
}

// ─────────────────────────────────────────────────────────────────────────────
// Charts tab
// ─────────────────────────────────────────────────────────────────────────────

function ChartsTab({ deploymentId }: { deploymentId: string }) {
  const [rows, setRows] = useState<ObsRow[]>([])
  const [loading, setLoading] = useState(true)
  const [chartType, setChartType] = useState<ChartType>('bar')
  const [selectedSpecies, setSelectedSpecies] = useState<Set<string>>(new Set())
  const chartRef = useRef<HTMLDivElement>(null)

  // Context for the zero-observation state: a deployment with photos and no
  // detections is a real monitoring result ("nothing came past"), so the page
  // shows the period and effort instead of a bare "no data" void.
  const [zeroContext, setZeroContext] = useState<{
    start: string | null
    end: string | null
    mediaCount: number
  } | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      supabase
        .from('observations')
        .select('created_at, scientific_name, observation_type')
        .eq('deployment_id', deploymentId)
        .is('deleted_at', null)
        .not('scientific_name', 'is', null)
        .order('created_at'),
      supabase
        .from('deployments')
        .select('deployment_start, deployment_end')
        .eq('id', deploymentId)
        .single(),
      supabase
        .from('media')
        .select('id', { count: 'exact', head: true })
        .eq('deployment_id', deploymentId)
        .is('deleted_at', null),
    ]).then(([obs, dep, media]) => {
      // supabase-js resolves failures into { error } rather than rejecting -
      // without this check a permissions or network failure would render as
      // "0 animal observations", a false zero (review, #113).
      const err = obs.error || dep.error || media.error
      if (err) {
        setLoadError(err.message)
        setLoading(false)
        return
      }
      setLoadError(null)
      setRows(obs.data || [])
      setZeroContext({
        start: dep.data?.deployment_start ?? null,
        end: dep.data?.deployment_end ?? null,
        mediaCount: media.count ?? 0,
      })
      setLoading(false)
    })
      // supabase-js resolves query errors into { error } (handled above), so
      // a rejection here is an exception in the .then callback or a genuine
      // transport failure. Route it into the same error card - clearing only
      // the spinner would fall through to the zero state, a false zero.
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
  }, [deploymentId])

  const allSpecies = [...new Set(rows.map((r) => r.scientific_name!).filter(Boolean))].sort()

  useEffect(() => {
    if (allSpecies.length > 0 && selectedSpecies.size === 0) {
      setSelectedSpecies(new Set(allSpecies.slice(0, 6)))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allSpecies.join(',')])

  const activeSpecies = allSpecies.filter((s) => selectedSpecies.has(s))

  const toggleSpecies = (sp: string) =>
    setSelectedSpecies((prev) => {
      const next = new Set(prev)
      if (next.has(sp)) { next.delete(sp) } else { next.add(sp) }
      return next
    })

  // Filtered rows for the chart (only active species)
  const filteredRows = useMemo(
    () => rows.filter((r) => r.scientific_name && activeSpecies.includes(r.scientific_name)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, activeSpecies.join(',')],
  )

  // Vega-Lite spec — rebuilds only when data, active species, or chart type changes
  const spec = useMemo(
    () => buildObsSpec(filteredRows, activeSpecies, chartType),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filteredRows, activeSpecies.join(','), chartType],
  )

  const downloadCsv = () => {
    const csv = csvFromRows(filteredRows, activeSpecies)
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url
    a.download = `observations-${deploymentId}-${toIso(new Date())}.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  const downloadPng = () => {
    const pkg = 'html-to-image'
    import(/* @vite-ignore */ pkg)
      .then((mod: { toPng: (el: HTMLElement) => Promise<string> }) => {
        if (!chartRef.current) return
        mod.toPng(chartRef.current).then((dataUrl) => {
          const a = document.createElement('a'); a.href = dataUrl
          a.download = `chart-${deploymentId}-${toIso(new Date())}.png`
          a.click()
        })
      })
      .catch(() => alert('PNG export: run `npm i html-to-image` first.'))
  }

  if (loading) return <div style={{ padding: '2rem', opacity: 0.5 }}>Loading observations…</div>

  if (loadError) {
    return (
      <div className="glass-card" style={{ padding: '2rem', textAlign: 'center' }}>
        <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>⚠️</div>
        <p style={{ margin: '0 0 0.25rem', fontWeight: 600 }}>Couldn&apos;t load this deployment&apos;s results</p>
        <p style={{ margin: 0, fontSize: '0.875rem', opacity: 0.65 }}>{loadError}</p>
      </div>
    )
  }

  if (rows.length === 0) {
    const fmtD = (iso: string | null) =>
      iso ? new Date(iso).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : null
    const start = fmtD(zeroContext?.start ?? null)
    const end = fmtD(zeroContext?.end ?? null)
    const mediaCount = zeroContext?.mediaCount ?? 0
    return (
      <div className="glass-card" style={{ padding: '2.5rem', textAlign: 'center' }}>
        <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>📊</div>
        <h4 style={{ margin: '0 0 1rem' }}>
          {mediaCount > 0 ? '0 animal observations in this deployment period' : 'No data for this deployment yet'}
        </h4>
        <div style={{ display: 'flex', gap: '2rem', justifyContent: 'center', flexWrap: 'wrap', marginBottom: '1.25rem' }}>
          {start && (
            <div>
              <div style={{ fontSize: '0.75rem', opacity: 0.55, textTransform: 'uppercase' }}>Monitoring period</div>
              <div style={{ fontWeight: 600 }}>{start} → {end ?? 'ongoing'}</div>
            </div>
          )}
          <div>
            <div style={{ fontSize: '0.75rem', opacity: 0.55, textTransform: 'uppercase' }}>Photos analysed</div>
            <div style={{ fontWeight: 600 }}>{mediaCount}</div>
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', opacity: 0.55, textTransform: 'uppercase' }}>Animal observations</div>
            <div style={{ fontWeight: 600 }}>0</div>
          </div>
        </div>
        <p style={{ fontSize: '0.875rem', opacity: 0.65, maxWidth: '46ch', margin: '0 auto 0.75rem' }}>
          {mediaCount > 0
            ? 'A deployment with photos and no detections is a valid result — nothing came past the camera, or the AI missed it. If you spot an animal the AI missed, label it and it will appear here.'
            : 'Upload this deployment’s photos and the results will appear here after analysis.'}
        </p>
        <Link to={`/annotations?deployment=${deploymentId}`} style={{ color: 'var(--primary)', fontSize: '0.9rem' }}>
          {mediaCount > 0 ? 'Review the photos in Annotations →' : 'Go to Annotations →'}
        </Link>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Controls */}
      <div className="glass-card" style={{ padding: '1rem 1.25rem', display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Chart type toggle */}
        <div style={{ display: 'flex', gap: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
          {(['bar', 'line'] as ChartType[]).map((t) => (
            <button
              key={t}
              onClick={() => setChartType(t)}
              style={{
                padding: '0.3rem 0.85rem', fontSize: '0.75rem', cursor: 'pointer', border: 'none',
                background: chartType === t ? 'var(--primary)' : 'transparent',
                color: chartType === t ? '#fff' : 'var(--text-color)',
              }}
            >
              {t === 'bar' ? '▌ Bar' : '/ Line'}
            </button>
          ))}
        </div>

        {/* Species toggles */}
        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', flex: 1 }}>
          {allSpecies.map((sp, i) => (
            <button
              key={sp}
              onClick={() => toggleSpecies(sp)}
              style={{
                fontSize: '0.7rem', padding: '0.2rem 0.55rem',
                border: `1px solid ${selectedSpecies.has(sp) ? COLORS[i % COLORS.length] : 'var(--border)'}`,
                borderRadius: 12, cursor: 'pointer',
                background: selectedSpecies.has(sp) ? `${COLORS[i % COLORS.length]}22` : 'transparent',
                color: selectedSpecies.has(sp) ? COLORS[i % COLORS.length] : 'var(--text-color)',
                fontStyle: 'italic',
              }}
            >
              {sp}
            </button>
          ))}
        </div>

        {/* Export buttons */}
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={downloadCsv} style={{ fontSize: '0.75rem', padding: '0.3rem 0.7rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--primary)', cursor: 'pointer' }}>
            ⬇ CSV
          </button>
          <button onClick={downloadPng} style={{ fontSize: '0.75rem', padding: '0.3rem 0.7rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--primary)', cursor: 'pointer' }}>
            🖼 PNG
          </button>
        </div>
      </div>

      {/* Chart */}
      <div className="glass-card" style={{ padding: '1.25rem' }} ref={chartRef}>
        <div style={{ fontSize: '0.8125rem', opacity: 0.6, marginBottom: '0.75rem' }}>
          Observations per day · {rows.length} total
        </div>
        {filteredRows.length === 0 ? (
          <div style={{ opacity: 0.5, textAlign: 'center', padding: '2rem' }}>No data for selected species.</div>
        ) : (
          <VegaChart spec={spec} />
        )}
      </div>

      {/* Summary table */}
      <div className="glass-card" style={{ padding: '1.25rem', overflowX: 'auto' }}>
        <h4 style={{ marginTop: 0, marginBottom: '0.75rem' }}>Species totals</h4>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid var(--border)' }}>
              <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Species</th>
              <th style={{ textAlign: 'right', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Observations</th>
            </tr>
          </thead>
          <tbody>
            {allSpecies.map((sp) => {
              const count = rows.filter((r) => r.scientific_name === sp).length
              return (
                <tr key={sp} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.35rem 0.5rem', fontStyle: 'italic' }}>{sp}</td>
                  <td style={{ padding: '0.35rem 0.5rem', textAlign: 'right' }}>{count}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Exports tab
// ─────────────────────────────────────────────────────────────────────────────

interface ExportCard {
  id: string
  title: string
  desc: string
  icon: string
  ext: string
  format: string
  real?: boolean
}

const EXPORT_CARDS: ExportCard[] = [
  { id: 'camtrap', title: 'CamtrapDP Zip Package', icon: '📦', ext: 'zip', format: 'CamtrapDP Package (ZIP)',
    desc: 'Frictionless Camtrap Data Package with standardised JSON descriptors matching Darwin Core standards.', real: true },
  { id: 'events', title: 'Ecological Events CSV', icon: '📊', ext: 'csv', format: 'Ecological Events (CSV)',
    desc: 'Normalised temporal events with observation duration, taxonomic abundance counts and sensor efforts.' },
  { id: 'media', title: 'Media Observations CSV', icon: '📸', ext: 'csv', format: 'Media Observations (CSV)',
    desc: 'Raw media observation records with coordinates, AI prediction confidences and bounding boxes.' },
  { id: 'dwc', title: 'Darwin Core GBIF Zip', icon: '🧬', ext: 'zip', format: 'Darwin Core GBIF Archive (ZIP)',
    desc: 'Ready-to-upload Occurrences zip mapping local taxa identifiers to the GBIF taxonomy.' },
]

function ExportsTab({ deploymentId }: { deploymentId: string }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [logs, setLogs] = useState<Array<{ id: string; format: string; created_at: string }>>([])

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 4000) }

  const trigger = useCallback(async (card: ExportCard) => {
    setBusy(card.id)
    try {
      if (card.real) {
        const { data: dep } = await supabase
          .from('deployments')
          .select('project_id')
          .eq('id', deploymentId)
          .single()
        if (!dep) throw new Error('Deployment not found')
        const { data, error } = await supabase.functions.invoke('export-camtrap-dp', {
          body: { project_id: dep.project_id },
        })
        if (error) throw new Error(error.message)
        const blob = data instanceof Blob ? data : new Blob([data], { type: 'application/zip' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a'); a.href = url
        a.download = `camtrapdp-${dep.project_id}-${toIso(new Date())}.zip`
        a.click(); URL.revokeObjectURL(url)
      } else {
        const content = `deployment_id,format\n${deploymentId},${card.format}`
        const blob = new Blob([content], { type: 'text/plain' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a'); a.href = url
        a.download = `${card.id}-${deploymentId}.${card.ext}`
        a.click(); URL.revokeObjectURL(url)
      }
      const entry = { id: `exp-${Date.now()}`, format: card.format, created_at: new Date().toISOString() }
      setLogs((prev) => [entry, ...prev.slice(0, 9)])
      showToast(`${card.format} downloaded`)
    } catch (err: unknown) {
      showToast(`Export failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(null)
    }
  }, [deploymentId])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {toast && (
        <div style={{ padding: '0.75rem 1rem', borderRadius: 'var(--radius)', background: 'rgba(76,175,80,0.12)', border: '1px solid rgba(76,175,80,0.3)', fontSize: '0.875rem', color: '#4caf50' }}>
          ✓ {toast}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
        {EXPORT_CARDS.map((card) => {
          const isBusy = busy === card.id
          return (
            <div
              key={card.id}
              className="glass-card"
              style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem', cursor: 'pointer', transition: 'transform 0.15s', border: isBusy ? '1px solid var(--primary)' : undefined }}
              onClick={() => !isBusy && trigger(card)}
              onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateY(-2px)' }}
              onMouseLeave={(e) => { e.currentTarget.style.transform = 'translateY(0)' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: '1.75rem' }}>{card.icon}</span>
                <span style={{ fontSize: '0.65rem', fontFamily: 'monospace', opacity: 0.45 }}>
                  .{card.ext.toUpperCase()}
                  {card.real && <span style={{ marginLeft: 4, color: '#4caf50' }}>●</span>}
                </span>
              </div>
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.35rem' }}>{card.title}</div>
                <div style={{ fontSize: '0.775rem', opacity: 0.65, lineHeight: 1.4 }}>{card.desc}</div>
              </div>
              <button
                className="btn"
                disabled={isBusy}
                style={{ marginTop: 'auto', background: isBusy ? 'transparent' : 'rgba(255,255,255,0.03)', border: isBusy ? '1px solid var(--primary)' : '1px solid var(--border)', fontSize: '0.8125rem' }}
              >
                {isBusy ? '⏳ Preparing…' : `Download ${card.ext.toUpperCase()}`}
              </button>
            </div>
          )
        })}
      </div>
      {logs.length > 0 && (
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <h4 style={{ marginTop: 0, marginBottom: '0.75rem', fontSize: '0.875rem' }}>Recent downloads</h4>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '0.35rem 0.5rem' }}>Format</th>
                <th style={{ textAlign: 'left', padding: '0.35rem 0.5rem' }}>Downloaded</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.35rem 0.5rem' }}>{l.format}</td>
                  <td style={{ padding: '0.35rem 0.5rem', opacity: 0.65 }}>{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page root
// ─────────────────────────────────────────────────────────────────────────────

export function ReportingPage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<PageTab>('charts')

  if (!deployment_id) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', color: 'var(--text-color)' }}>
      {/* Header */}
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h3 style={{ margin: 0 }}>Deployment results</h3>
          <span style={{ fontSize: '0.8125rem', opacity: 0.65 }}>Species diversity, activity & exports · {deployment_id.slice(0, 8)}…</span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-outline" onClick={() => navigate('/insights')}
            style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', fontSize: '0.8125rem' }}>
            My Data
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '2px solid var(--border)' }}>
        {([['charts', '📊 Charts'], ['exports', '📦 Exports']] as [PageTab, string][]).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              padding: '0.5rem 1.25rem', border: 'none',
              borderBottom: tab === id ? '2px solid var(--primary)' : '2px solid transparent',
              background: 'transparent', color: tab === id ? 'var(--primary)' : 'var(--text-color)',
              fontWeight: tab === id ? 600 : 400, cursor: 'pointer', marginBottom: '-2px', fontSize: '0.875rem',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'charts'  && <ChartsTab  deploymentId={deployment_id} />}
      {tab === 'exports' && <ExportsTab deploymentId={deployment_id} />}
    </div>
  )
}
