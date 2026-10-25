/**
 * chartSpec: the Vega-Lite specs of the Insights > Reports charts. Each takes a small table
 * already counted by lib/observationSummary, never the raw observations (UI audit 04 #8).
 */
import { VEGA_CONFIG, withBarLabels, type Spec } from '../../lib/vegaSpec'
import type { CountRow, DayRow } from '../../lib/observationSummary'
import { OTHER_SPECIES } from '../../lib/observationSummary'

const SCHEMA = 'https://vega.github.io/schema/vega-lite/v5.json'
const BAR_COLOUR = '#4caf50'

/** Horizontal bars, one per row, most first, each with its count at the end. */
export function countBarSpec(rows: CountRow[], opts: { title: string; italic?: boolean }): Spec {
  return withBarLabels({
    $schema: SCHEMA,
    width: 'container',
    height: { step: 22 },
    data: { values: rows },
    mark: { type: 'bar', cornerRadiusEnd: 3 },
    encoding: {
      y: {
        field: 'label', type: 'nominal', sort: '-x', title: null,
        axis: { labelLimit: 220, ...(opts.italic ? { labelFontStyle: 'italic' } : {}) },
      },
      x: { field: 'count', type: 'quantitative', title: 'Count' },
      color: { value: BAR_COLOUR },
      tooltip: [{ field: 'label', type: 'nominal', title: opts.title }, { field: 'count', type: 'quantitative', title: 'Count' }],
    },
    config: VEGA_CONFIG,
  }, 'h')
}

const TYPE_DOMAIN = ['animal', 'human', 'vehicle', 'blank', 'unknown']
const TYPE_RANGE = ['#4caf50', '#2196f3', '#ff9800', '#9e9e9e', '#607d8b']

/** Observation types as a donut. */
export function typeArcSpec(rows: CountRow[]): Spec {
  return {
    $schema: SCHEMA,
    width: 'container',
    height: 260,
    data: { values: rows },
    mark: { type: 'arc', innerRadius: 50, outerRadius: 100 },
    encoding: {
      theta: { field: 'count', type: 'quantitative' },
      color: { field: 'label', type: 'nominal', scale: { domain: TYPE_DOMAIN, range: TYPE_RANGE }, legend: { title: null } },
      tooltip: [{ field: 'label', type: 'nominal', title: 'Type' }, { field: 'count', type: 'quantitative', title: 'Count' }],
    },
    config: VEGA_CONFIG,
  }
}

const SPECIES_COLOURS = ['#4caf50', '#2196f3', '#ff9800', '#e91e63', '#9c27b0', '#00bcd4', '#ff5722', '#8bc34a']
const OTHER_COLOUR = '#9e9e9e'

/**
 * Detections per day, stacked by species (the Reporting page's chart, UI audit decision 5).
 * `species` is the legend order; "Other species" is grey.
 */
export function perDaySpec(rows: DayRow[], species: string[]): Spec {
  return {
    $schema: SCHEMA,
    width: 'container',
    height: 280,
    data: { values: rows },
    mark: { type: 'bar', cornerRadiusTopLeft: 2, cornerRadiusTopRight: 2 },
    encoding: {
      // The day is a calendar date already taken in the deployment's zone: read it as UTC
      // so the browser's own zone cannot move it.
      x: { field: 'day', type: 'temporal', timeUnit: 'utcyearmonthdate', title: null, axis: { labelAngle: -35, format: '%d %b' } },
      y: { field: 'count', type: 'quantitative', aggregate: 'sum', title: 'Detections' },
      color: {
        field: 'species', type: 'nominal',
        scale: { domain: species, range: species.map((s, i) => (s === OTHER_SPECIES ? OTHER_COLOUR : SPECIES_COLOURS[i % SPECIES_COLOURS.length])) },
        legend: { title: null, labelFontStyle: 'italic', orient: 'bottom' },
      },
      tooltip: [
        { field: 'day', type: 'temporal', timeUnit: 'utcyearmonthdate', title: 'Date' },
        { field: 'species', type: 'nominal', title: 'Species' },
        { field: 'count', type: 'quantitative', title: 'Detections' },
      ],
    },
    config: VEGA_CONFIG,
  }
}
