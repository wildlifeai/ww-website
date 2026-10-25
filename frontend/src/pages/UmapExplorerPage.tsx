import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useUmapCoords, type UmapPoint } from '../hooks/useBrain'
import { FeatureOff } from '../components/common/FeatureOff'
import { isFeatureDisabled } from '../lib/featureFlags'

const PALETTE = ['#5cdb7a', '#3dcfb8', '#5b9cf6', '#a882f5', '#e87abd', '#e8b84b', '#e87054', '#8effa8', '#82c8f5', '#f5a3d0']
const W = 900
const H = 600
const PAD = 30

function colorFor(clusterId: number): string {
  if (clusterId < 0) return '#e05c5c' // outliers
  return PALETTE[clusterId % PALETTE.length]
}

// NOTE: canvas handles a few thousand points smoothly. For 100k+ swap to
// deck.gl ScatterplotLayer (GPU) — same data shape, drop-in upgrade.
export function UmapExplorerPage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const navigate = useNavigate()
  const { data, isLoading, error } = useUmapCoords(deployment_id)
  const points = useMemo(() => data?.points || [], [data])
  const photosUrl = `/annotations?deployment=${deployment_id}`

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const projectedRef = useRef<Array<{ sx: number; sy: number; p: UmapPoint }>>([])
  const [hover, setHover] = useState<{ left: number; top: number; p: UmapPoint } | null>(null)

  const bounds = useMemo(() => {
    if (points.length === 0) return null
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (const p of points) {
      minX = Math.min(minX, p.umap_x); maxX = Math.max(maxX, p.umap_x)
      minY = Math.min(minY, p.umap_y); maxY = Math.max(maxY, p.umap_y)
    }
    return { minX, maxX, minY, maxY }
  }, [points])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !bounds) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, W, H)

    const spanX = bounds.maxX - bounds.minX || 1
    const spanY = bounds.maxY - bounds.minY || 1
    const projected: Array<{ sx: number; sy: number; p: UmapPoint }> = []

    for (const p of points) {
      const sx = PAD + ((p.umap_x - bounds.minX) / spanX) * (W - 2 * PAD)
      const sy = PAD + (1 - (p.umap_y - bounds.minY) / spanY) * (H - 2 * PAD)
      projected.push({ sx, sy, p })
      ctx.beginPath()
      ctx.arc(sx, sy, 3, 0, Math.PI * 2)
      if (p.is_outlier) {
        ctx.strokeStyle = '#e05c5c'
        ctx.lineWidth = 1.5
        ctx.stroke()
      } else {
        ctx.fillStyle = colorFor(p.cluster_id)
        ctx.fill()
      }
    }
    projectedRef.current = projected
  }, [points, bounds])

  // The point under the pointer (within 8 px), in canvas and on-screen coordinates.
  const pointAt = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const mx = ((e.clientX - rect.left) / rect.width) * W
    const my = ((e.clientY - rect.top) / rect.height) * H
    let best: { sx: number; sy: number; p: UmapPoint } | null = null
    let bestD = 64 // 8px²
    for (const pt of projectedRef.current) {
      const d = (pt.sx - mx) ** 2 + (pt.sy - my) ** 2
      if (d < bestD) { bestD = d; best = pt }
    }
    return best ? { left: (best.sx / W) * rect.width, top: (best.sy / H) * rect.height, p: best.p } : null
  }

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => setHover(pointAt(e))

  // A click opens the deployment's photos and names the photo in `media`, for the grid to open.
  const openPhoto = (p: UmapPoint) => navigate(`${photosUrl}&media=${p.media_id}`)
  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const hit = pointAt(e)
    if (hit) openPhoto(hit.p)
  }
  // Enter opens the point under the pointer, as a click does.
  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (e.key === 'Enter' && hover) openPhoto(hover.p)
  }

  const clusterCount = useMemo(() => new Set(points.filter((p) => !p.is_outlier).map((p) => p.cluster_id)).size, [points])

  if (isLoading) return <div style={{ padding: '2rem' }}>Loading embedding map…</div>
  if (isFeatureDisabled(error)) return <FeatureOff backTo={photosUrl} backLabel="Back to the photos" />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', color: 'var(--text-color)' }}>
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1rem 1.25rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h3 style={{ margin: 0 }}>UMAP Explorer</h3>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>{points.length} points · {clusterCount} clusters · coloured by cluster</span>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <Link to={`/clusters/${deployment_id}`} className="btn btn-outline" style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>Clusters</Link>
          <Link to={photosUrl} className="btn btn-outline" style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>Photos</Link>
        </div>
      </div>

      {error ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: 'var(--error)' }}>
          Could not load the map: {error.message}
        </div>
      ) : points.length === 0 ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          No embeddings yet. Run the Wildlife Brain from the Clusters page.
        </div>
      ) : (
        <div className="card" style={{ padding: '1rem', position: 'relative' }}>
          <canvas
            ref={canvasRef}
            width={W}
            height={H}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            onClick={onClick}
            onKeyDown={onKeyDown}
            tabIndex={0}
            aria-label="Photos placed by how alike they look, coloured by cluster. Click a point to open its photo."
            style={{ width: '100%', height: 'auto', display: 'block', cursor: hover ? 'pointer' : 'crosshair', borderRadius: 'var(--radius)' }}
          />
          {hover && (
            <div style={{ position: 'absolute', left: hover.left + 12, top: hover.top, pointerEvents: 'none', background: 'rgba(0,0,0,0.85)', color: '#fff', padding: '4px 8px', borderRadius: 4, fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
              {hover.p.is_outlier ? 'outlier' : `cluster ${hover.p.cluster_id}`}
              {hover.p.cluster_purity ? ` · ${hover.p.cluster_purity}` : ''}
              <br />
              Click to open the photo
            </div>
          )}
        </div>
      )}
    </div>
  )
}
