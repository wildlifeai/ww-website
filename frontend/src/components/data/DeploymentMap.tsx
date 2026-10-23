import { useEffect, useMemo } from 'react'
import { MapContainer, TileLayer, Marker, Tooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { startDateLabel } from '../../lib/deploymentFilterOptions'
import { boundsOf, spreadOffsets, type Bounds } from '../../lib/mapMarkers'

interface Deployment {
  id: string
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
  deployment_end: string | null
  /** IANA zone the dates are shown in; the browser's when absent. */
  timezone?: string | null
  project_name?: string
  observation_count?: number
  // Optional analytics, supplied by the Insights Map tab. When omitted (e.g. the
  // Field page) markers fall back to the plain project-coloured / count behaviour.
  metricValue?: number   // value used for circle sizing (total or per-day)
  perDay?: number        // detections per active day
  activeDays?: number    // deployment duration in days (min 1)
  present?: boolean      // is the filtered species present here? undefined ⇒ n/a
  speciesCounts?: Record<string, number>  // species → detection count, for the default pie markers
}

interface DeploymentMapProps {
  deployments: Deployment[]
  onSelectDeployment?: (id: string) => void
  selectedDeploymentId?: string | null
  metric?: 'total' | 'perDay'
  speciesLabel?: string | null   // when set, the map is in present/absent mode
  showSpeciesPie?: boolean        // opt-in: render species pie markers (Insights). Other pages keep plain circles.
}

// Plain mode (no species): one colour per project, with a legend while the colours do not repeat.
const PROJECT_COLOURS = [
  '#4caf50', '#2196f3', '#ff9800', '#e91e63',
  '#9c27b0', '#00bcd4', '#ff5722', '#607d8b',
]
// Species mode colours by presence only (UI audit 04 #13).
const PRESENT_COLOUR = '#4caf50'

// A distinct palette for species pie wedges; species beyond it fall back to grey ("Other").
const SPECIES_COLOURS = [
  '#4caf50', '#2196f3', '#ff9800', '#e91e63', '#9c27b0',
  '#00bcd4', '#ffc107', '#795548', '#3f51b5', '#8bc34a',
]
const OTHER_COLOUR = '#9e9e9e'

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }
const LEGEND_ITEM: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: '0.375rem' }

/** A legend dot; `hollow` draws the absent-site ring. */
function swatch(colour: string, hollow = false) {
  return (
    <span style={{
      width: 10, height: 10, borderRadius: '50%', display: 'inline-block',
      backgroundColor: hollow ? 'rgba(158,158,158,0.12)' : colour,
      border: hollow ? `1.5px solid ${colour}` : undefined,
    }} />
  )
}

// ── Marker SVG helpers ───────────────────────────────────────────────────────
function polar(cx: number, cy: number, r: number, frac: number): [number, number] {
  const a = 2 * Math.PI * frac - Math.PI / 2 // start at 12 o'clock
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)]
}
function wedge(cx: number, cy: number, r: number, start: number, end: number): string {
  const [x0, y0] = polar(cx, cy, r, start)
  const [x1, y1] = polar(cx, cy, r, end)
  const large = end - start > 0.5 ? 1 : 0
  return `M${cx},${cy} L${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} Z`
}
function svg(size: number, inner: string): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`
}
function pieMarkerHtml(
  counts: Record<string, number>,
  colourOf: (sp: string) => string,
  size: number,
  selected: boolean,
): string {
  const r = size / 2
  const stroke = selected ? '#111' : '#fff'
  const sw = selected ? 2 : 1
  const entries = Object.entries(counts).filter(([, c]) => c > 0).sort((a, b) => b[1] - a[1])
  const total = entries.reduce((s, [, c]) => s + c, 0)
  let inner: string
  if (total === 0) {
    // No detections (yet): small hollow grey ring.
    inner = `<circle cx="${r}" cy="${r}" r="${r - 2}" fill="${OTHER_COLOUR}" fill-opacity="0.12" stroke="${OTHER_COLOUR}" stroke-width="1.5"/>`
  } else if (entries.length === 1) {
    inner = `<circle cx="${r}" cy="${r}" r="${r - sw}" fill="${colourOf(entries[0][0])}" fill-opacity="0.9" stroke="${stroke}" stroke-width="${sw}"/>`
  } else {
    let acc = 0
    inner = entries.map(([sp, c]) => {
      const start = acc / total; acc += c; const end = acc / total
      return `<path d="${wedge(r, r, r - sw, start, end)}" fill="${colourOf(sp)}" fill-opacity="0.9" stroke="${stroke}" stroke-width="${sw}"/>`
    }).join('')
  }
  return svg(size, inner)
}
function circleMarkerHtml(radius: number, colour: string, fillOpacity: number, stroke: string, weight: number): string {
  const c = radius + weight
  return svg(2 * c, `<circle cx="${c}" cy="${c}" r="${radius}" fill="${colour}" fill-opacity="${fillOpacity}" stroke="${stroke}" stroke-width="${weight}"/>`)
}

// One L.DivIcon per distinct look and offset, so a re-render or a selection change hands
// react-leaflet the same object for every marker that did not change, and it skips setIcon.
const iconCache = new Map<string, L.DivIcon>()
function cachedIcon(html: string, size: number, [dx, dy]: [number, number]): L.DivIcon {
  const key = `${size}|${dx}|${dy}|${html}`
  let icon = iconCache.get(key)
  if (!icon) {
    if (iconCache.size > 2000) iconCache.clear()
    // The anchor moves the icon by the fan-out offset; the tooltip follows it.
    icon = L.divIcon({
      html, className: 'ww-map-marker', iconSize: [size, size],
      iconAnchor: [size / 2 - dx, size / 2 - dy], tooltipAnchor: [dx, dy],
    })
    iconCache.set(key, icon)
  }
  return icon
}

// Frames every marker on load, and again when the set of sites changes; it leaves the user's
// pan and zoom alone otherwise.
function FitBounds({ south, west, north, east }: Bounds) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds([[south, west], [north, east]], { padding: [32, 32], maxZoom: 13 })
  }, [map, south, west, north, east])
  return null
}

const NO_OFFSET: [number, number] = [0, 0]

function dateRange(d: Deployment): string | null {
  const start = startDateLabel(d.deployment_start, d.timezone)
  const end = startDateLabel(d.deployment_end, d.timezone)
  if (!start && !end) return null
  return end ? `${start ?? '?'} to ${end}` : `from ${start}`
}

export function DeploymentMap({ deployments, onSelectDeployment, selectedDeploymentId, metric = 'total', speciesLabel = null, showSpeciesPie = false }: DeploymentMapProps) {
  const mapped = useMemo(
    () => deployments.filter(d => d.latitude != null && d.longitude != null) as (Deployment & { latitude: number; longitude: number })[],
    [deployments],
  )
  // Pie markers only when explicitly enabled (Insights) AND not in single-species present/absent mode.
  const pieMode = showSpeciesPie && speciesLabel == null
  const speciesMode = speciesLabel != null
  const metricLabel = metric === 'perDay' ? 'detections per day' : 'detections'

  // Plain mode colours by project; the legend lists them only while no colour repeats.
  const projectNames = useMemo(() => [...new Set(deployments.map(d => d.project_name ?? ''))], [deployments])

  // Species → colour, ranked by total detections across all sites (stable legend + wedge order).
  const { speciesColour, rankedSpecies, overflowSpecies } = useMemo(() => {
    const totals: Record<string, number> = {}
    for (const d of mapped) for (const [sp, c] of Object.entries(d.speciesCounts ?? {})) totals[sp] = (totals[sp] ?? 0) + c
    const ranked = Object.entries(totals).sort((a, b) => b[1] - a[1]).map(([sp]) => sp)
    const map: Record<string, string> = {}
    ranked.forEach((sp, i) => { map[sp] = i < SPECIES_COLOURS.length ? SPECIES_COLOURS[i] : OTHER_COLOUR })
    return {
      speciesColour: (sp: string) => map[sp] ?? OTHER_COLOUR,
      rankedSpecies: ranked.slice(0, SPECIES_COLOURS.length),
      overflowSpecies: ranked.length > SPECIES_COLOURS.length,
    }
  }, [mapped])

  const bounds = useMemo(() => boundsOf(mapped), [mapped])
  const offsets = useMemo(() => spreadOffsets(mapped), [mapped])

  // One icon per marker, rebuilt only when the data, the mode or the selection changes.
  const icons = useMemo(() => {
    const sizeValue = (d: Deployment) => d.metricValue ?? d.observation_count ?? 0
    // A site counts as "absent" only when a species is chosen and it has no detections there.
    // The size scale is driven by the present sites only, so a swarm of absent (zero) sites
    // can't flatten the contrast between the present ones.
    const isAbsent = (d: Deployment) => speciesMode && d.present === false
    const maxVal = Math.max(...mapped.filter(d => !isAbsent(d)).map(sizeValue), 1)
    const colourOfProject = (name: string) => PROJECT_COLOURS[projectNames.indexOf(name) % PROJECT_COLOURS.length]
    return new Map(mapped.map(d => {
      const selected = d.id === selectedDeploymentId
      const offset = offsets.get(d.id) ?? NO_OFFSET
      if (pieMode) {
        const size = 22 + Math.round((sizeValue(d) / maxVal) * 22) // 22–44px
        return [d.id, cachedIcon(pieMarkerHtml(d.speciesCounts ?? {}, speciesColour, size, selected), size, offset)]
      }
      const absent = isAbsent(d)
      const colour = absent ? OTHER_COLOUR : speciesMode ? PRESENT_COLOUR : colourOfProject(d.project_name ?? '')
      const radius = absent ? 5 : 8 + Math.round((sizeValue(d) / maxVal) * 12)
      const weight = selected ? 3 : 1.5
      const stroke = selected ? '#fff' : colour
      const html = circleMarkerHtml(radius, colour, absent ? 0.12 : 0.8, stroke, weight)
      return [d.id, cachedIcon(html, 2 * (radius + weight), offset)]
    }))
  }, [mapped, offsets, pieMode, speciesMode, speciesColour, projectNames, selectedDeploymentId])

  const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2))
  const gpsNote = `${mapped.length} of ${deployments.length} deployment${deployments.length !== 1 ? 's' : ''} have GPS`

  if (!bounds) {
    return (
      <div style={{
        height: '420px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'var(--surface)',
        borderRadius: 'var(--radius)',
        border: '1px solid var(--border)',
        fontSize: '0.875rem',
        ...MUTED,
      }}>
        No deployments with GPS coordinates to display.
      </div>
    )
  }

  return (
    <div style={{ borderRadius: 'var(--radius)', overflow: 'hidden', border: '1px solid var(--border)' }}>
      {/* Strip the default white box/border Leaflet puts on divIcon markers */}
      <style>{`.ww-map-marker { background: none; border: none; }`}</style>
      <MapContainer
        bounds={[[bounds.south, bounds.west], [bounds.north, bounds.east]]}
        boundsOptions={{ padding: [32, 32], maxZoom: 13 }}
        style={{ height: '420px', width: '100%' }}
        scrollWheelZoom={true}
      >
        <FitBounds {...bounds} />
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {mapped.map(d => {
          const obsCount = d.observation_count ?? 0
          const range = dateRange(d)
          const absent = speciesMode && d.present === false
          const topSpecies = pieMode ? Object.entries(d.speciesCounts ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 5) : []
          return (
            <Marker
              key={d.id}
              position={[d.latitude, d.longitude]}
              icon={icons.get(d.id)}
              zIndexOffset={d.id === selectedDeploymentId ? 1000 : 0}
              eventHandlers={{ click: () => onSelectDeployment?.(d.id) }}
            >
              <Tooltip>
                <strong>{d.location_name ?? 'Unnamed'}</strong><br />
                {d.project_name && <span>{d.project_name}<br /></span>}
                {pieMode ? (
                  obsCount === 0 ? (
                    <em>No detections yet</em>
                  ) : (
                    <>
                      <span>{obsCount} detection{obsCount !== 1 ? 's' : ''}
                        {metric === 'perDay' && d.perDay != null && <>, {fmt(d.perDay)} per day</>}</span>
                      {topSpecies.map(([sp, c]) => (
                        <span key={sp} style={{ display: 'block', marginTop: 1 }}>
                          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: speciesColour(sp), marginRight: 4 }} />
                          {sp}: {c}
                        </span>
                      ))}
                    </>
                  )
                ) : (
                  <>
                    {range && <>{range}<br /></>}
                    {speciesMode && (absent ? (
                      <span><em>{speciesLabel}</em>: not detected</span>
                    ) : (
                      <span>
                        <em>{speciesLabel}</em>: {obsCount} detection{obsCount !== 1 ? 's' : ''}
                        {d.activeDays != null && <span> over {d.activeDays} day{d.activeDays !== 1 ? 's' : ''}</span>}
                        {d.perDay != null && <><br />{fmt(d.perDay)} per day</>}
                      </span>
                    ))}
                  </>
                )}
              </Tooltip>
            </Marker>
          )
        })}
      </MapContainer>

      {/* Legend */}
      <div style={{
        padding: '0.625rem 0.75rem',
        display: 'flex',
        gap: '1rem',
        flexWrap: 'wrap',
        backgroundColor: 'var(--surface)',
        borderTop: '1px solid var(--border)',
        fontSize: '0.75rem',
      }}>
        {pieMode && (
          <>
            {rankedSpecies.length === 0 && <span style={MUTED}>No species detected yet</span>}
            {rankedSpecies.map(sp => (
              <span key={sp} style={LEGEND_ITEM}>{swatch(speciesColour(sp))}<em>{sp}</em></span>
            ))}
            {overflowSpecies && <span style={LEGEND_ITEM}>{swatch(OTHER_COLOUR)}Other</span>}
          </>
        )}
        {speciesMode && (
          <>
            <span style={LEGEND_ITEM}>{swatch(PRESENT_COLOUR)}Detected</span>
            <span style={LEGEND_ITEM}>{swatch(OTHER_COLOUR, true)}Not detected</span>
          </>
        )}
        {!pieMode && !speciesMode && projectNames.length <= PROJECT_COLOURS.length && projectNames.map((name, i) => (
          <span key={name} style={LEGEND_ITEM}>{swatch(PROJECT_COLOURS[i])}{name || 'Unknown project'}</span>
        ))}
        <span style={{ marginLeft: 'auto', ...MUTED }}>
          {pieMode || speciesMode ? `Size: ${metricLabel}. ` : ''}{gpsNote}
        </span>
      </div>
    </div>
  )
}
