/**
 * ObservationReports — fixed default dashboard for the Reports tab.
 *
 * Charts are rendered with Vega-Lite via the <VegaChart> primitive.
 * All aggregation happens inside Vega-Lite transforms (no useMemo pivots).
 *
 * This fixed summary is still used by MyData. The editable Insights ▸ Reports
 * dashboard (ReportsDashboard) reuses these types and the shared chartSpec.
 */
import { useMemo } from 'react'
import { VegaChart } from '../ui/VegaChart'
import { VEGA_CONFIG, withBarLabels } from '../../lib/vegaSpec'

// ─────────────────────────────────────────────────────────────────────────────
// Types (exported so the reports dashboard / chartSpec can reuse)
// ─────────────────────────────────────────────────────────────────────────────

export interface Observation {
  id: string
  deployment_id: string
  scientific_name: string | null
  observation_type: string | null
  created_at: string
}

export interface Deployment {
  id: string
  location_name: string | null
  deployment_start: string | null
  deployment_end: string | null
}

interface ObservationReportsProps {
  observations: Observation[]
  deployments: Deployment[]
  loading?: boolean
}

// ─────────────────────────────────────────────────────────────────────────────
// Spec builders
// ─────────────────────────────────────────────────────────────────────────────

function speciesBarSpec(observations: Observation[]) {
  return withBarLabels({
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    height: 280,
    data: { values: observations },
    transform: [
      { filter: 'datum.scientific_name != null && datum.scientific_name !== ""' },
      {
        aggregate: [{ op: 'count', as: 'count' }],
        groupby: ['scientific_name'],
      },
      {
        window: [{ op: 'rank', as: 'rank' }],
        sort: [{ field: 'count', order: 'descending' }],
      },
      { filter: 'datum.rank <= 15' },
    ],
    mark: { type: 'bar', cornerRadiusEnd: 3 },
    encoding: {
      y: {
        field: 'scientific_name',
        type: 'nominal',
        sort: '-x',
        title: null,
        axis: { labelFontStyle: 'italic', labelLimit: 160 },
      },
      x: {
        field: 'count',
        type: 'quantitative',
        title: 'Observations',
      },
      color: {
        field: 'scientific_name',
        type: 'nominal',
        legend: null,
        scale: { scheme: 'tableau10' },
      },
      tooltip: [
        { field: 'scientific_name', title: 'Species' },
        { field: 'count', title: 'Observations' },
      ],
    },
    config: VEGA_CONFIG,
  }, 'h')
}

function typeArcSpec(observations: Observation[]) {
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    height: 280,
    data: { values: observations },
    transform: [
      {
        calculate: "datum.observation_type || 'unknown'",
        as: 'type',
      },
    ],
    mark: { type: 'arc', innerRadius: 50, outerRadius: 100 },
    encoding: {
      theta: { aggregate: 'count', type: 'quantitative' },
      color: {
        field: 'type',
        type: 'nominal',
        scale: {
          domain: ['animal', 'human', 'vehicle', 'blank', 'unknown'],
          range: ['#4caf50', '#2196f3', '#ff9800', '#9e9e9e', '#607d8b'],
        },
        legend: { title: null },
      },
      tooltip: [
        { field: 'type', title: 'Type' },
        { aggregate: 'count', title: 'Count' },
      ],
    },
    config: VEGA_CONFIG,
  }
}

function deploymentBarSpec(enriched: Array<Observation & { location_name: string }>) {
  return withBarLabels({
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    height: 220,
    data: { values: enriched },
    transform: [
      {
        aggregate: [{ op: 'count', as: 'count' }],
        groupby: ['location_name'],
      },
      {
        window: [{ op: 'rank', as: 'rank' }],
        sort: [{ field: 'count', order: 'descending' }],
      },
      { filter: 'datum.rank <= 15' },
    ],
    mark: { type: 'bar', cornerRadiusTopLeft: 3, cornerRadiusTopRight: 3 },
    encoding: {
      x: {
        field: 'location_name',
        type: 'nominal',
        sort: '-y',
        title: null,
        axis: { labelAngle: -35, labelLimit: 120 },
      },
      y: {
        field: 'count',
        type: 'quantitative',
        title: 'Observations',
      },
      color: { value: '#4caf50' },
      tooltip: [
        { field: 'location_name', title: 'Deployment' },
        { field: 'count', title: 'Observations' },
      ],
    },
    config: VEGA_CONFIG,
  }, 'v')
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function ObservationReports({
  observations,
  deployments,
  loading,
}: ObservationReportsProps) {
  // Enrich observations with location_name for the deployment bar chart
  const enriched = useMemo(() => {
    const depMap = Object.fromEntries(
      deployments.map((d) => [d.id, d.location_name ?? d.id.slice(0, 8)]),
    )
    return observations.map((o) => ({
      ...o,
      location_name: depMap[o.deployment_id] ?? o.deployment_id.slice(0, 8),
    }))
  }, [observations, deployments])

  // KPI totals
  const { totalObs, uniqueSpecies, activeDeployments, animalCount } = useMemo(
    () => ({
      totalObs: observations.length,
      uniqueSpecies: new Set(
        observations
          .map((o) => o.scientific_name)
          .filter((s): s is string => !!s && s !== '(unidentified)'),
      ).size,
      activeDeployments: new Set(observations.map((o) => o.deployment_id)).size,
      animalCount: observations.filter((o) => o.observation_type === 'animal').length,
    }),
    [observations],
  )

  // Memoise specs (stable reference → VegaChart only re-embeds when data changes)
  const barSpec   = useMemo(() => speciesBarSpec(observations),  [observations])
  const arcSpec   = useMemo(() => typeArcSpec(observations),     [observations])
  const depSpec   = useMemo(() => deploymentBarSpec(enriched),   [enriched])

  if (loading) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', opacity: 0.5, fontSize: '0.875rem' }}>
        Loading observations…
      </div>
    )
  }

  if (observations.length === 0) {
    return (
      <div style={{
        padding: '3rem',
        textAlign: 'center',
        opacity: 0.5,
        fontSize: '0.875rem',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        backgroundColor: 'var(--surface)',
      }}>
        No observations found. Add observations to your deployments to see reports here.
      </div>
    )
  }

  const PANEL: React.CSSProperties = {
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '1.25rem',
  }

  const HEADING: React.CSSProperties = {
    fontSize: '0.875rem',
    fontWeight: 600,
    marginBottom: '0.75rem',
    opacity: 0.85,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.75rem' }}>
        {([
          ['Total Observations', totalObs],
          ['Unique Species',     uniqueSpecies],
          ['Active Deployments', activeDeployments],
          ['Animal Records',     animalCount],
        ] as [string, number][]).map(([label, value]) => (
          <div key={label} style={{ ...PANEL, textAlign: 'center', padding: '1rem' }}>
            <div style={{ fontSize: '1.875rem', fontWeight: 700, color: 'var(--primary)' }}>
              {value.toLocaleString()}
            </div>
            <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Species bar + type pie */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '1.25rem' }}>
        <div style={PANEL}>
          <div style={HEADING}>Top Species by Observation Count</div>
          <VegaChart spec={barSpec} />
        </div>
        <div style={PANEL}>
          <div style={HEADING}>Observation Types</div>
          <VegaChart spec={arcSpec} />
        </div>
      </div>

      {/* Deployments bar */}
      {enriched.length > 0 && (
        <div style={PANEL}>
          <div style={HEADING}>Observations per Deployment Location</div>
          <VegaChart spec={depSpec} />
        </div>
      )}
    </div>
  )
}
