/**
 * Vega-Lite spec helpers shared by the charts (components/ui/VegaChart.tsx and its callers).
 * Kept out of the component file so it only exports components (fast refresh).
 */

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
