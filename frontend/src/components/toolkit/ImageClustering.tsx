import { Fragment, useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient, API_BASE_URL } from '../../lib/apiClient'

interface ClusterMember {
  filename: string
  sharpness: number
  width: number
  height: number
  is_representative: boolean
  roi?: number[] | null
}

interface Cluster {
  cluster_id: number
  size: number
  representative: string
  members: ClusterMember[]
}

interface ClusteringResult {
  total_images: number
  total_clusters: number
  total_representatives: number
  clusters: Cluster[]
}

interface ApiEnvelope<T> {
  data: T
  meta?: any
  error?: any
}

export function ImageClustering() {
  const [files, setFiles] = useState<File[]>([])
  const [threshold, setThreshold] = useState(10)
  const [roiCropForHashing, setRoiCropForHashing] = useState(false)
  const [result, setResult] = useState<ClusteringResult | null>(null)
  const [expandedCluster, setExpandedCluster] = useState<number | null>(null)
  const [repThumbs, setRepThumbs] = useState<Record<number, string>>({})

  const clusterMutation = useMutation({
    mutationFn: async (imageFiles: File[]) => {
      const formData = new FormData()
      for (const f of imageFiles) {
        formData.append('files', f)
      }
      formData.append('max_hamming', String(threshold))
  if (roiCropForHashing) formData.append('roi_crop_for_hashing', 'true')
  // Keep behavior opinionated + efficient: if ROI-cropped hashing is enabled,
  // use per-frame ROI tracking.
  formData.append('roi_per_frame', 'true')

  const envelope = (await apiClient.upload('/api/clustering/analyze', formData)) as ApiEnvelope<ClusteringResult>
  return (envelope?.data ?? (envelope as any)) as ClusteringResult
    },
    onSuccess: (data) => setResult(data),
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const imageFiles = Array.from(e.target.files).filter(
        (f) =>
          f.type.startsWith('image/') ||
          f.name.toLowerCase().endsWith('.jpg') ||
          f.name.toLowerCase().endsWith('.jpeg') ||
          f.name.toLowerCase().endsWith('.png')
      )
      setFiles(imageFiles)
      setResult(null)
    }
  }

  const downloadCsv = async () => {
    const formData = new FormData()
    for (const f of files) {
      formData.append('files', f)
    }
    formData.append('max_hamming', String(threshold))

    try {
      const response = await fetch(`${API_BASE_URL}/api/clustering/analyze/csv`, {
        method: 'POST',
        body: formData,
      })
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'clustering.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('CSV download failed', e)
    }
  }

  const downloadRoiDebugZip = async () => {
    const formData = new FormData()
    for (const f of files) {
      formData.append('files', f)
    }
    formData.append('max_frames', String(Math.min(12, files.length)))

    try {
      const response = await fetch(`${API_BASE_URL}/api/clustering/roi-debug.zip`, {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        const text = await response.text().catch(() => '')
        throw new Error(
          `ROI debug download failed (${response.status}). ${text ? text.slice(0, 200) : ''}`
        )
      }
      const ct = response.headers.get('content-type') || ''
      if (!ct.includes('application/zip')) {
        const text = await response.text().catch(() => '')
        throw new Error(
          `ROI debug download did not return a ZIP (content-type: ${ct}). ${text ? text.slice(0, 200) : ''}`
        )
      }

      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'roi_debug_bundle.zip'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('ROI debug zip download failed', e)
      alert((e as Error)?.message ?? 'ROI debug zip download failed')
    }
  }

  const fileMap = useMemo(() => {
    const m = new Map<string, File>()
    for (const f of files) m.set(f.name, f)
    return m
  }, [files])

  useEffect(() => {
    // Reset thumbnails when inputs/results change.
    setRepThumbs({})
  }, [files, result])

  const buildRepThumb = async (clusterId: number, member: ClusterMember) => {
    const f = fileMap.get(member.filename)
    if (!f) return
    if (!member.roi || member.roi.length !== 4) return

  const [x0, y0, x1, y1] = member.roi
    const targetSize = 96 // small visual cue

    const imgEl = new Image()
    const url = URL.createObjectURL(f)
    try {
      await new Promise<void>((resolve, reject) => {
        imgEl.onload = () => resolve()
        imgEl.onerror = () => reject(new Error('image load failed'))
        imgEl.src = url
      })

  // Build an overlay thumbnail: render a downscaled full frame, then draw the ROI box on top.
  const maxDim = targetSize
  const scale = maxDim / Math.max(imgEl.naturalWidth, imgEl.naturalHeight)
  const outW = Math.max(1, Math.round(imgEl.naturalWidth * scale))
  const outH = Math.max(1, Math.round(imgEl.naturalHeight * scale))

      const canvas = document.createElement('canvas')
      canvas.width = outW
      canvas.height = outH
      const ctx = canvas.getContext('2d')
      if (!ctx) return

  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(imgEl, 0, 0, outW, outH)

  // Draw ROI rectangle in scaled coordinates
  const rx0 = x0 * scale
  const ry0 = y0 * scale
  const rw = Math.max(1, (x1 - x0) * scale)
  const rh = Math.max(1, (y1 - y0) * scale)

  ctx.strokeStyle = 'rgba(255,0,0,0.95)'
  ctx.lineWidth = 2
  ctx.strokeRect(rx0, ry0, rw, rh)

  // A subtle dark mask outside the ROI makes the box read better at tiny sizes.
  ctx.fillStyle = 'rgba(0,0,0,0.25)'
  ctx.fillRect(0, 0, outW, ry0)
  ctx.fillRect(0, ry0, rx0, rh)
  ctx.fillRect(rx0 + rw, ry0, outW - (rx0 + rw), rh)
  ctx.fillRect(0, ry0 + rh, outW, outH - (ry0 + rh))

      const dataUrl = canvas.toDataURL('image/jpeg', 0.75)
      setRepThumbs((prev) => ({ ...prev, [clusterId]: dataUrl }))
    } finally {
      URL.revokeObjectURL(url)
    }
  }

  return (
    <div>
      <h4 style={{ marginBottom: '0.25rem' }}>🔬 Near-Duplicate Clustering</h4>
      <p style={{ opacity: 0.6, fontSize: '0.8125rem', marginBottom: '1rem' }}>
        Group similar images and select representatives for iNaturalist upload.
        The algorithm uses perceptual hashing with a BK-tree index for fast
        similarity search.
      </p>

      {/* File input */}
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: '200px' }}>
          <label style={{ fontSize: '0.75rem', fontWeight: 500, display: 'block', marginBottom: '0.25rem' }}>
            Upload images
          </label>
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp"
            onChange={handleFileSelect}
            style={{ fontSize: '0.8125rem', width: '100%' }}
          />
        </div>

        <div style={{ minWidth: '180px' }}>
          <label style={{ fontSize: '0.75rem', fontWeight: 500, display: 'block', marginBottom: '0.25rem' }}>
            Similarity threshold: {threshold}
          </label>
          <input
            type="range"
            min={0}
            max={20}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.625rem', opacity: 0.5 }}>
            <span>Strict</span>
            <span>Loose</span>
          </div>
        </div>

    <div style={{ minWidth: '260px' }}>
          <label style={{ fontSize: '0.75rem', fontWeight: 500, display: 'block', marginBottom: '0.25rem' }}>
            ROI-cropped hashing
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8125rem' }}>
            <input
              type="checkbox"
              checked={roiCropForHashing}
              onChange={(e) => setRoiCropForHashing(e.target.checked)}
            />
            Use motion ROI crop for clustering
          </label>
          <div style={{ fontSize: '0.6875rem', opacity: 0.6, marginTop: '0.25rem' }}>
      When enabled, each frame is cropped to its own motion bbox before hashing, so background doesn’t dominate clustering.
          </div>
        </div>

        <button
          className="btn"
          disabled={files.length === 0 || clusterMutation.isPending}
          onClick={() => clusterMutation.mutate(files)}
          style={{
            padding: '0.5rem 1.25rem',
            fontSize: '0.8125rem',
            whiteSpace: 'nowrap',
          }}
        >
          {clusterMutation.isPending
            ? `Clustering ${files.length} images…`
            : `Cluster ${files.length > 0 ? files.length : ''} Images`}
        </button>
      </div>

      {clusterMutation.isError && (
        <p style={{ color: 'var(--error)', fontSize: '0.8125rem', marginTop: '0.75rem' }}>
          {(clusterMutation.error as Error).message}
        </p>
      )}

      {/* Results */}
      {result && (
        <div style={{ marginTop: '1.5rem' }}>
          {/* Summary cards */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
              gap: '0.75rem',
              marginBottom: '1.25rem',
            }}
          >
            <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>{result.total_images}</div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>Images</div>
            </div>
            <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>{result.total_clusters}</div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>Clusters</div>
            </div>
            <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>{result.total_representatives}</div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>Representatives</div>
            </div>
            <div className="card" style={{ padding: '0.75rem', textAlign: 'center' }}>
              <div style={{ fontSize: '1.5rem', fontWeight: 700 }}>
                {result.total_images > 0
                  ? `${Math.round((1 - result.total_representatives / result.total_images) * 100)}%`
                  : '0%'}
              </div>
              <div style={{ fontSize: '0.75rem', opacity: 0.6 }}>Reduction</div>
            </div>
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
            <button
              className="btn"
              onClick={downloadCsv}
              style={{
                fontSize: '0.75rem',
                padding: '0.375rem 0.75rem',
                background: 'var(--surface-2)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
              }}
            >
              📥 Download CSV
            </button>

            <button
              className="btn"
              disabled={files.length === 0}
              onClick={downloadRoiDebugZip}
              style={{
                fontSize: '0.75rem',
                padding: '0.375rem 0.75rem',
                background: 'var(--surface-2)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
              }}
            >
              Download ROI debug bundle (zip)
            </button>
          </div>

          {/* Cluster table */}
          <div style={{ overflowX: 'auto' }}>
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: '0.8125rem',
              }}
            >
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border)', textAlign: 'left' }}>
                  <th style={{ padding: '0.5rem', width: '60px' }}>Cluster</th>
                  <th style={{ padding: '0.5rem', width: '60px' }}>Size</th>
                  <th style={{ padding: '0.5rem', width: '110px' }}>Preview</th>
                  <th style={{ padding: '0.5rem' }}>Representative</th>
                  <th style={{ padding: '0.5rem', width: '100px' }}>Sharpness</th>
                  <th style={{ padding: '0.5rem', width: '50px' }}></th>
                </tr>
              </thead>
              <tbody>
                {result.clusters.map((cluster) => {
                  const rep = cluster.members.find((m) => m.is_representative)
                  const isExpanded = expandedCluster === cluster.cluster_id

                  // Build rep thumb lazily (avoid doing canvas work before results exist).
                  if (roiCropForHashing && rep && rep.roi && !repThumbs[cluster.cluster_id]) {
                    // Fire-and-forget; component state update will re-render.
                    void buildRepThumb(cluster.cluster_id, rep)
                  }

                  return (
                    <Fragment key={cluster.cluster_id}>
                      <tr
                        style={{
                          borderBottom: '1px solid var(--border)',
                          cursor: cluster.size > 1 ? 'pointer' : undefined,
                          background: isExpanded ? 'var(--surface-2)' : undefined,
                        }}
                        onClick={() =>
                          cluster.size > 1 &&
                          setExpandedCluster(isExpanded ? null : cluster.cluster_id)
                        }
                      >
                        <td style={{ padding: '0.5rem', fontFamily: 'monospace' }}>
                          #{cluster.cluster_id}
                        </td>
                        <td style={{ padding: '0.5rem' }}>
                          <span
                            style={{
                              background:
                                cluster.size > 3
                                  ? 'var(--primary)'
                                  : 'var(--surface-2)',
                              color: cluster.size > 3 ? '#fff' : 'var(--text)',
                              padding: '0.125rem 0.5rem',
                              borderRadius: '1rem',
                              fontSize: '0.75rem',
                              fontWeight: 600,
                            }}
                          >
                            {cluster.size}
                          </span>
                        </td>

                        <td style={{ padding: '0.5rem' }}>
                          {roiCropForHashing ? (
                            repThumbs[cluster.cluster_id] ? (
                              <img
                                src={repThumbs[cluster.cluster_id]}
                                alt="rep crop"
                                style={{
                                  width: '96px',
                                  height: '96px',
                                  objectFit: 'contain',
                                  borderRadius: '6px',
                                  border: '1px solid var(--border)',
                                  background: 'var(--surface-2)',
                                }}
                              />
                            ) : (
                              <div
                                style={{
                                  width: '96px',
                                  height: '96px',
                                  borderRadius: '6px',
                                  border: '1px solid var(--border)',
                                  background: 'var(--surface-2)',
                                  opacity: 0.6,
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  fontSize: '0.6875rem',
                                }}
                              >
                                {rep?.roi ? 'Loading…' : 'No ROI'}
                              </div>
                            )
                          ) : (
                            <div style={{ fontSize: '0.75rem', opacity: 0.5 }}>—</div>
                          )}
                        </td>
                        <td
                          style={{
                            padding: '0.5rem',
                            fontFamily: 'monospace',
                            fontSize: '0.75rem',
                          }}
                        >
                          ⭐ {cluster.representative}
                        </td>
                        <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>
                          {rep ? rep.sharpness.toFixed(1) : '—'}
                        </td>
                        <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                          {cluster.size > 1 && (
                            <span style={{ fontSize: '0.75rem', opacity: 0.5 }}>
                              {isExpanded ? '▲' : '▼'}
                            </span>
                          )}
                        </td>
                      </tr>
                      {isExpanded &&
                        cluster.members
                          .filter((m) => !m.is_representative)
                          .map((member) => (
                            <tr
                              key={`${cluster.cluster_id}-${member.filename}`}
                              style={{
                                borderBottom: '1px solid var(--border)',
                                background: 'var(--surface-2)',
                                fontSize: '0.75rem',
                              }}
                            >
                              <td></td>
                              <td></td>
                              <td></td>
                              <td
                                style={{
                                  padding: '0.375rem 0.5rem',
                                  fontFamily: 'monospace',
                                  opacity: 0.7,
                                  paddingLeft: '1.5rem',
                                }}
                              >
                                {member.filename}
                              </td>
                              <td
                                style={{ padding: '0.375rem 0.5rem', opacity: 0.7 }}
                              >
                                {member.sharpness.toFixed(1)}
                              </td>
                              <td></td>
                            </tr>
                          ))}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
