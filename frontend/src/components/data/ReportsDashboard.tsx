/**
 * ReportsDashboard — the editable Insights ▸ Reports dashboard.
 *
 * Everything (counters, charts, tables) is a serialisable Widget rendered from a
 * single registry, seeded with DEFAULT_WIDGETS (the former fixed report). With
 * "Edit dashboard" on, every widget — including the defaults — can be edited,
 * resized, reordered, removed, or added to. Layout is in-memory only for now
 * (no per-user persistence); "Reset" returns to the default.
 */
import { useMemo, useState } from 'react'
import { VegaChart } from '../ui/VegaChart'
import type { Observation, Deployment } from './ObservationReports'
import {
  buildVegaSpec, CHART_TYPE_OPTIONS, GROUP_BY_OPTIONS, GROUP_BY_LABELS,
  type ChartType, type GroupBy,
} from './chartSpec'

// ── Widget model ──────────────────────────────────────────────────────

type WidgetKind = 'counter' | 'chart' | 'table'
type CounterMetric = 'total_obs' | 'unique_species' | 'active_deployments' | 'animal_count'
type Width = 'full' | 'half' | 'third' | 'quarter'

interface Widget {
  id: string
  kind: WidgetKind
  title: string
  width: Width
  metric?: CounterMetric   // counter
  chartType?: ChartType    // chart
  groupBy?: GroupBy        // chart + table
}

const METRIC_OPTIONS: { value: CounterMetric; label: string }[] = [
  { value: 'total_obs',         label: 'Total observations' },
  { value: 'unique_species',    label: 'Unique species' },
  { value: 'active_deployments', label: 'Active deployments' },
  { value: 'animal_count',      label: 'Animal detections' },
]

const WIDTH_OPTIONS: { value: Width; label: string }[] = [
  { value: 'quarter', label: '¼ width' },
  { value: 'third',   label: '⅓ width' },
  { value: 'half',    label: '½ width' },
  { value: 'full',    label: 'Full width' },
]

const WIDTH_BASIS: Record<Width, string> = {
  full: '100%', half: 'calc(50% - 0.5rem)', third: 'calc(33.333% - 0.67rem)', quarter: 'calc(25% - 0.75rem)',
}

const DEFAULT_WIDGETS: Widget[] = [
  { id: 'w-total',   kind: 'counter', title: 'Total Observations', width: 'quarter', metric: 'total_obs' },
  { id: 'w-species', kind: 'counter', title: 'Unique Species',     width: 'quarter', metric: 'unique_species' },
  { id: 'w-deps',    kind: 'counter', title: 'Active Deployments', width: 'quarter', metric: 'active_deployments' },
  { id: 'w-animals', kind: 'counter', title: 'Animal Detections',  width: 'quarter', metric: 'animal_count' },
  { id: 'w-topsp',   kind: 'chart',   title: 'Top Species by Observation Count', width: 'half', chartType: 'bar_h', groupBy: 'scientific_name' },
  { id: 'w-types',   kind: 'chart',   title: 'Observation Types',  width: 'half', chartType: 'arc', groupBy: 'observation_type' },
  { id: 'w-perdep',  kind: 'chart',   title: 'Observations per Deployment Location', width: 'full', chartType: 'bar_v', groupBy: 'deployment' },
]

const PANEL: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1rem',
}

// ── Data helpers ──────────────────────────────────────────────────────

interface EnrichedObs extends Observation { location_name: string }

function counterValue(metric: CounterMetric, obs: Observation[]): number {
  switch (metric) {
    case 'total_obs':          return obs.length
    case 'unique_species':     return new Set(obs.map(o => o.scientific_name).filter((s): s is string => !!s && s !== '(unidentified)')).size
    case 'active_deployments': return new Set(obs.map(o => o.deployment_id)).size
    case 'animal_count':       return obs.filter(o => o.observation_type === 'animal').length
  }
}

function tableRows(groupBy: GroupBy, enriched: EnrichedObs[]): { label: string; count: number }[] {
  const keyOf = (o: EnrichedObs): string =>
    groupBy === 'deployment' ? o.location_name
    : groupBy === 'month' ? (o.created_at ?? '').slice(0, 7)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    : ((o as any)[groupBy] as string)
  const counts: Record<string, number> = {}
  for (const o of enriched) { const k = keyOf(o) || '—'; counts[k] = (counts[k] ?? 0) + 1 }
  return Object.entries(counts).map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count)
}

// ── Widget renderer ───────────────────────────────────────────────────

function WidgetBody({ widget, obs, enriched }: { widget: Widget; obs: Observation[]; enriched: EnrichedObs[] }) {
  if (widget.kind === 'counter') {
    return (
      <div style={{ textAlign: 'center', padding: '0.5rem 0' }}>
        <div style={{ fontSize: '1.875rem', fontWeight: 700, color: 'var(--primary)' }}>
          {counterValue(widget.metric ?? 'total_obs', obs).toLocaleString()}
        </div>
        <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>{widget.title}</div>
      </div>
    )
  }
  if (widget.kind === 'table') {
    const rows = tableRows(widget.groupBy ?? 'scientific_name', enriched)
    return (
      <>
        <div style={HEADING}>{widget.title}</div>
        <div style={{ maxHeight: 280, overflowY: 'auto' }}>
          {/* Sized to its content, so each count sits beside its name rather than at the far
              edge of a wide widget (#191); numbers stay right-aligned so digits line up. */}
          <table style={{ fontSize: '0.875rem', borderCollapse: 'collapse' }}>
            <thead><tr style={{ opacity: 0.75, textAlign: 'left' }}><th style={{ padding: '0.25rem 2rem 0.25rem 0' }}>{GROUP_BY_LABELS[widget.groupBy ?? 'scientific_name']}</th><th style={{ textAlign: 'right' }}>Count</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.label} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.25rem 2rem 0.25rem 0' }}>{r.label}</td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{r.count}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={2} style={{ opacity: 0.5, padding: '0.5rem 0' }}>No data</td></tr>}
            </tbody>
          </table>
        </div>
      </>
    )
  }
  // chart
  const spec = buildVegaSpec(
    { id: widget.id, title: widget.title, chartType: widget.chartType ?? 'bar_h', groupBy: widget.groupBy ?? 'scientific_name' },
    enriched,
  )
  return (
    <>
      <div style={HEADING}>{widget.title}</div>
      <VegaChart spec={spec} />
    </>
  )
}

const HEADING: React.CSSProperties = { fontWeight: 600, fontSize: '0.85rem', marginBottom: '0.5rem' }

// ── Edit form ─────────────────────────────────────────────────────────

const SELECT: React.CSSProperties = {
  padding: '0.35rem 0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8rem',
}

function WidgetForm({ initial, onSave, onCancel }: { initial: Widget; onSave: (w: Widget) => void; onCancel: () => void }) {
  const [w, setW] = useState<Widget>(initial)
  const set = (p: Partial<Widget>) => setW(prev => ({ ...prev, ...p }))
  return (
    <div style={{ ...PANEL, display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
      <input value={w.title} onChange={e => set({ title: e.target.value })} placeholder="Title" style={{ ...SELECT, fontWeight: 600 }} />
      <label style={{ fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>Type
        <select value={w.kind} onChange={e => set({ kind: e.target.value as WidgetKind })} style={SELECT}>
          <option value="counter">Counter</option>
          <option value="chart">Chart</option>
          <option value="table">Table</option>
        </select>
      </label>
      {w.kind === 'counter' && (
        <label style={{ fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>Metric
          <select value={w.metric ?? 'total_obs'} onChange={e => set({ metric: e.target.value as CounterMetric })} style={SELECT}>
            {METRIC_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      )}
      {w.kind === 'chart' && (
        <label style={{ fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>Chart type
          <select value={w.chartType ?? 'bar_h'} onChange={e => set({ chartType: e.target.value as ChartType })} style={SELECT}>
            {CHART_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      )}
      {(w.kind === 'chart' || w.kind === 'table') && (
        <label style={{ fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>Group by
          <select value={w.groupBy ?? 'scientific_name'} onChange={e => set({ groupBy: e.target.value as GroupBy })} style={SELECT}>
            {GROUP_BY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      )}
      <label style={{ fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>Width
        <select value={w.width} onChange={e => set({ width: e.target.value as Width })} style={SELECT}>
          {WIDTH_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
      <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
        <button onClick={onCancel} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', opacity: 0.6 }}>Cancel</button>
        <button className="btn" onClick={() => onSave(w)} style={{ fontSize: '0.8rem' }}>Save</button>
      </div>
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────

interface Props { observations: Observation[]; deployments: Deployment[]; loading?: boolean }

let _seq = 0
const newId = () => `w-${Date.now()}-${_seq++}`

export function ReportsDashboard({ observations, deployments, loading }: Props) {
  const [widgets, setWidgets] = useState<Widget[]>(DEFAULT_WIDGETS)
  const [editing, setEditing] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)

  const enriched = useMemo<EnrichedObs[]>(() => {
    const depMap = Object.fromEntries(deployments.map(d => [d.id, d.location_name ?? d.id.slice(0, 8)]))
    return observations.map(o => ({ ...o, location_name: depMap[o.deployment_id] ?? o.deployment_id.slice(0, 8) }))
  }, [observations, deployments])

  const move = (id: string, dir: -1 | 1) => setWidgets(ws => {
    const i = ws.findIndex(w => w.id === id); const j = i + dir
    if (i < 0 || j < 0 || j >= ws.length) return ws
    const next = [...ws];[next[i], next[j]] = [next[j], next[i]]; return next
  })
  const remove = (id: string) => setWidgets(ws => ws.filter(w => w.id !== id))
  const save = (w: Widget) => { setWidgets(ws => ws.some(x => x.id === w.id) ? ws.map(x => x.id === w.id ? w : x) : [...ws, w]); setEditId(null) }
  const addWidget = () => { const w: Widget = { id: newId(), kind: 'chart', title: 'New chart', width: 'half', chartType: 'bar_h', groupBy: 'scientific_name' }; setWidgets(ws => [...ws, w]); setEditId(w.id) }

  if (loading) {
    return <div style={{ padding: '3rem', textAlign: 'center', opacity: 0.5, fontSize: '0.875rem' }}>Loading observations…</div>
  }

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem' }}>
        <button onClick={() => { setEditing(e => !e); setEditId(null) }} className="btn"
          style={{ fontSize: '0.8rem', background: editing ? 'var(--primary)' : 'transparent', color: editing ? '#fff' : 'var(--text-color)', border: '1px solid var(--border)' }}>
          {editing ? '✓ Done editing' : '✏️ Edit dashboard'}
        </button>
        {editing && <>
          <button onClick={addWidget} className="btn" style={{ fontSize: '0.8rem' }}>+ Add widget</button>
          <button onClick={() => { setWidgets(DEFAULT_WIDGETS); setEditId(null) }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--primary)', marginLeft: 'auto' }}>↺ Reset to default</button>
        </>}
      </div>

      {/* Widget grid */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem' }}>
        {widgets.map((w, i) => (
          <div key={w.id} style={{ flex: `1 1 ${WIDTH_BASIS[w.width]}`, minWidth: w.kind === 'counter' ? 150 : 280 }}>
            {editId === w.id ? (
              <WidgetForm initial={w} onSave={save} onCancel={() => setEditId(null)} />
            ) : (
              <div style={{ ...PANEL, position: 'relative' }}>
                {editing && (
                  <div style={{ position: 'absolute', top: 4, right: 4, display: 'flex', gap: '0.15rem', zIndex: 2 }}>
                    <Tool onClick={() => move(w.id, -1)} disabled={i === 0} title="Move earlier">◀</Tool>
                    <Tool onClick={() => move(w.id, 1)} disabled={i === widgets.length - 1} title="Move later">▶</Tool>
                    <Tool onClick={() => setEditId(w.id)} title="Edit">✏️</Tool>
                    <Tool onClick={() => remove(w.id)} title="Remove">✕</Tool>
                  </div>
                )}
                <WidgetBody widget={w} obs={observations} enriched={enriched} />
              </div>
            )}
          </div>
        ))}
        {widgets.length === 0 && (
          <p style={{ opacity: 0.6, padding: '1rem' }}>No widgets. Click <strong>Edit dashboard → + Add widget</strong>, or Reset to default.</p>
        )}
      </div>
    </div>
  )
}

function Tool({ children, onClick, disabled, title }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; title: string }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      style={{ width: 22, height: 22, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem',
        border: '1px solid var(--border)', borderRadius: 4, background: 'var(--bg-color)', color: 'var(--text-color)',
        cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.35 : 0.85 }}>
      {children}
    </button>
  )
}
