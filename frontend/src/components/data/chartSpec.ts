/**
 * chartSpec — shared Vega-Lite spec builder + option metadata for the Reports
 * dashboard and the chart builder. Kept component-free so both can import it.
 */
import { VEGA_CONFIG } from '../ui/VegaChart'
import { withBarLabels } from '../../lib/vegaSpec'
import type { Observation } from './ObservationReports'

export type ChartType = 'bar_h' | 'bar_v' | 'arc' | 'line'
export type GroupBy = 'scientific_name' | 'observation_type' | 'deployment' | 'month'

export interface UserChartDef {
  id: string
  title: string
  chartType: ChartType
  groupBy: GroupBy
}

export interface EnrichedObs extends Observation {
  location_name: string
}

export const CHART_TYPE_OPTIONS: { value: ChartType; label: string }[] = [
  { value: 'bar_h', label: 'Horizontal bar' },
  { value: 'bar_v', label: 'Vertical bar' },
  { value: 'arc',   label: 'Pie / donut' },
  { value: 'line',  label: 'Line over time' },
]

export const GROUP_BY_OPTIONS: { value: GroupBy; label: string; hint?: string }[] = [
  { value: 'scientific_name',  label: 'Species (scientific name)' },
  { value: 'observation_type', label: 'Observation type' },
  { value: 'deployment',       label: 'Deployment location' },
  { value: 'month',            label: 'Month (trend over time)', hint: 'Best with Line chart' },
]

export const GROUP_BY_LABELS: Record<GroupBy, string> = {
  scientific_name:  'Species',
  observation_type: 'Observation type',
  deployment:       'Deployment',
  month:            'Month',
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function buildVegaSpec(chart: UserChartDef, data: EnrichedObs[]): Record<string, any> {
  const isMonth = chart.groupBy === 'month'
  const field = chart.groupBy === 'deployment'
    ? 'location_name'
    : chart.groupBy === 'month'
    ? 'created_at'
    : chart.groupBy

  const dimEncoding = isMonth
    ? { field, type: 'temporal', timeUnit: 'yearmonth', title: 'Month' }
    : {
        field,
        type: 'nominal',
        title: GROUP_BY_LABELS[chart.groupBy],
        ...(chart.groupBy === 'scientific_name' ? { axis: { labelFontStyle: 'italic', labelLimit: 160 } } : {}),
      }

  const transforms = []
  if (chart.groupBy === 'scientific_name') {
    transforms.push({ filter: 'datum.scientific_name != null && datum.scientific_name !== ""' })
  }
  if (chart.groupBy === 'deployment') {
    transforms.push({ filter: 'datum.location_name != null && datum.location_name !== ""' })
  }

  const countEnc = { aggregate: 'count', type: 'quantitative', title: 'Count' }

  const tooltip = [
    isMonth
      ? { field, type: 'temporal', timeUnit: 'yearmonth', title: 'Month' }
      : { field, type: 'nominal', title: GROUP_BY_LABELS[chart.groupBy] },
    { aggregate: 'count', title: 'Count' },
  ]

  const colorEnc = isMonth
    ? { value: '#4caf50' }
    : {
        field,
        type: 'nominal',
        scale: { scheme: 'tableau10' },
        legend: chart.chartType === 'arc' ? { title: null } : null,
      }

  const base = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    data: { values: data },
    transform: transforms,
    config: VEGA_CONFIG,
  }

  switch (chart.chartType) {
    case 'bar_h':
      return withBarLabels({
        ...base,
        height: 280,
        mark: { type: 'bar', cornerRadiusEnd: 3 },
        encoding: { y: { ...dimEncoding, sort: '-x', title: null }, x: countEnc, color: colorEnc, tooltip },
      }, 'h')
    case 'bar_v':
      return withBarLabels({
        ...base,
        height: 280,
        mark: { type: 'bar', cornerRadiusTopLeft: 3, cornerRadiusTopRight: 3 },
        encoding: { x: { ...dimEncoding, sort: '-y', title: null, axis: { ...(dimEncoding as { axis?: object }).axis, labelAngle: -35 } }, y: countEnc, color: colorEnc, tooltip },
      }, 'v')
    case 'arc':
      return {
        ...base,
        height: 260,
        mark: { type: 'arc', innerRadius: 50, outerRadius: 100 },
        encoding: { theta: countEnc, color: { field, type: 'nominal', scale: { scheme: 'tableau10' }, legend: { title: null } }, tooltip },
      }
    case 'line':
      return {
        ...base,
        height: 260,
        mark: { type: 'line', point: true, strokeWidth: 2 },
        encoding: { x: { ...dimEncoding, axis: { labelAngle: -35 } }, y: countEnc, color: isMonth ? undefined : { field, type: 'nominal', scale: { scheme: 'tableau10' }, legend: { title: null } }, tooltip },
      }
    default:
      return base
  }
}
