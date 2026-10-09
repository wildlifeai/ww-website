/**
 * Vega-Lite spec helpers shared by the charts (components/ui/VegaChart.tsx and its callers).
 * Kept out of the component file so it only exports components (fast refresh).
 */

// ─────────────────────────────────────────────────────────────────────────────
// Shared Vega config: sizes here, colours from the theme at embed time (themed)
// ─────────────────────────────────────────────────────────────────────────────

export const VEGA_CONFIG = {
  background: 'transparent',
  padding: 4,
  view: { stroke: 'transparent', fill: 'transparent' },
  axis: {
    labelFontSize: 13,
    titleFontSize: 13,
    tickColor: 'transparent',
  },
  legend: {
    labelFontSize: 12,
    titleFontSize: 12,
  },
  text: { fontSize: 12, fontWeight: 600 },
  mark: { tooltip: true },
  arc: {},
} as const

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Spec = Record<string, any>

/** Axis, grid, legend and label colours from the page's text colour. */
export function themed(config: Spec = {}, text: string): Spec {
  return {
    ...config,
    axis: { ...config.axis, labelColor: text, titleColor: text, gridColor: text, gridOpacity: 0.12, domainColor: text, domainOpacity: 0.3 },
    legend: { ...config.legend, labelColor: text, titleColor: text },
    text: { ...config.text, fill: text },
  }
}

/**
 * A single-mark bar chart as bars plus a value label at each bar's end (#191), so a count reads
 * at a glance or on a projected slide. `orientation` is the bars' direction: 'h' when the value
 * is on x. Colour stays on the bars only; the label takes the theme's text colour.
 */
export function withBarLabels(spec: Spec, orientation: 'h' | 'v'): Spec {
  const { mark, encoding, ...rest } = spec
  const { color, ...shared } = encoding
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { title, axis, ...value } = orientation === 'h' ? encoding.x : encoding.y
  const label = orientation === 'h'
    ? { type: 'text', align: 'left', baseline: 'middle', dx: 4 }
    : { type: 'text', align: 'center', baseline: 'bottom', dy: -3 }
  return {
    ...rest,
    encoding: shared,
    layer: [
      { mark, encoding: color ? { color } : {} },
      { mark: label, encoding: { text: value } },
    ],
  }
}
