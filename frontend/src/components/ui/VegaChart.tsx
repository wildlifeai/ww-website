/* eslint-disable react-refresh/only-export-components */
/**
 * VegaChart — thin React wrapper around vega-embed.
 *
 * Pass a plain Vega-Lite spec object and the component renders it inside
 * a div. The spec is re-embedded whenever its reference changes, so callers
 * should memoize specs with useMemo to avoid needless re-renders.
 *
 * Usage:
 *   const spec = useMemo(() => ({ ... }), [data])
 *   <VegaChart spec={spec} />
 *
 * Responsive width: include `"width": "container"` in the spec (Vega-Lite v5+)
 * and make the parent div 100% wide. The chart fills it automatically.
 *
 * Colours: axes, grid, legend and value labels take the page's text colour when the chart is
 * embedded, and the tooltip follows the light or dark scheme, so charts stay readable in dark
 * mode (#191). Hard-coded greys were dark-on-dark there.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react'
import embed from 'vega-embed'
import { themed, type Spec } from '../../lib/vegaSpec'

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

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

const DARK = '(prefers-color-scheme: dark)'
const subscribeScheme = (onChange: () => void) => {
  const mq = window.matchMedia(DARK)
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}
const prefersDark = () => window.matchMedia(DARK).matches

export interface VegaChartProps {
  /** A Vega-Lite spec. Use `useMemo` in the caller to stabilise the reference. */
  spec: Spec
  style?: React.CSSProperties
  className?: string
}

export function VegaChart({ spec, style, className }: VegaChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Re-embed when the system switches between light and dark.
  const dark = useSyncExternalStore(subscribeScheme, prefersDark, () => false)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    let cancelled = false

    let view: { finalize: () => void } | null = null
    const text = getComputedStyle(el).color

    embed(el, { ...spec, config: themed(spec.config, text) }, {
      // Show only the export menu (PNG/SVG) so users can save a chart for a report;
      // hide the source/compiled/editor actions to keep it clean.
      actions: { export: true, source: false, compiled: false, editor: false },
      downloadFileName: 'wildlife-watcher-chart',
      renderer: 'svg',
      tooltip: { theme: dark ? 'dark' : 'light' },
    })
      .then((result) => {
        if (cancelled) {
          result.view.finalize()
          return
        }
        view = result.view
      })
      .catch((err) => {
        if (!cancelled) console.error('[VegaChart] embed failed', err)
      })

    return () => {
      cancelled = true
      view?.finalize()
    }
  }, [spec, dark])

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ width: '100%', ...style }}
    />
  )
}
