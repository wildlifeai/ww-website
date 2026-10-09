import { useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useClusters, useUmapCoords, useConfirmCluster, useEmbedDeployment } from '../hooks/useBrain'
import type { ClusterAssignment } from '../hooks/useBrain'
import { useMediaRegistry } from '../hooks/useMediaRegistry'
import { apiClient } from '../lib/apiClient'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const PURITY_COLOR: Record<string, string> = { high: '#4caf50', medium: '#ff9800', low: '#f44336' }
const DEPTH_LABEL: Record<string, string> = { bulk: 'Bulk confirm', sample: 'Sample review', full: 'Full review' }

// ─────────────────────────────────────────────────────────────────────────────
// iNat publish hook — POST /api/inat/observations with selected media + taxon
// ─────────────────────────────────────────────────────────────────────────────

function useInatPublish() {
  return useMutation({
    mutationFn: async (args: { media_ids: string[]; scientific_name: string }) => {
      const r = (await apiClient.post('/api/inat/observations', args)) as { data: { submitted: number; observation_ids: string[] } }
      return r.data
    },
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Bulk reassign hook — POST /api/brain/review/{media_id} per item
// ─────────────────────────────────────────────────────────────────────────────

function useBulkReassign(deploymentId?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (args: { media_ids: string[]; scientific_name: string; decision: 'reassign' | 'expert' }) => {
      const results = await Promise.allSettled(
        args.media_ids.map((mid: string) =>
          apiClient.post(`/api/brain/review/${mid}`, {
            decision: args.decision,
            scientific_name: args.decision === 'reassign' ? args.scientific_name : undefined,
          })
        )
      )
      const ok = results.filter(r => r.status === 'fulfilled').length
      return { ok, failed: results.length - ok }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['brain', 'clusters', deploymentId] })
      qc.invalidateQueries({ queryKey: ['brain', 'review-queue', deploymentId] })
    },
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Thumb: a registry image, or the grid's no-thumbnail placeholder when the
// registry has no URL (no rendition yet and a private original, #305)
// ─────────────────────────────────────────────────────────────────────────────

function Thumb({ src, style }: { src: string | null | undefined; style: React.CSSProperties }) {
  if (!src) {
    return (
      <div style={{ ...style, background: 'rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ opacity: 0.3 }}>📷</span>
      </div>
    )
  }
  return <img src={src} alt="" style={style} />
}

// ─────────────────────────────────────────────────────────────────────────────
// PhotoGrid — expandable grid with checkbox selection
// ─────────────────────────────────────────────────────────────────────────────

function PhotoGrid({
  mediaIds,
  thumbById,
  selected,
  onToggle,
}: {
  mediaIds: string[]
  thumbById: Record<string, string | null>
  selected: Set<string>
  onToggle: (id: string) => void
}) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(72px, 1fr))',
      gap: 4,
      marginTop: '0.75rem',
      maxHeight: 340,
      overflowY: 'auto',
    }}>
      {mediaIds.map(mid => {
        const sel = selected.has(mid)
        return (
          <div
            key={mid}
            onClick={() => onToggle(mid)}
            title={mid}
            style={{
              position: 'relative', cursor: 'pointer',
              borderRadius: 4,
              outline: sel ? '2.5px solid var(--primary)' : '2.5px solid transparent',
              transition: 'outline 0.1s',
            }}
          >
            <Thumb
              src={thumbById[mid]}
              style={{ width: '100%', aspectRatio: '4/3', objectFit: 'cover', borderRadius: 4, display: 'block', background: 'var(--surface)' }}
            />
            {/* Checkbox overlay */}
            <div style={{
              position: 'absolute', top: 3, left: 3,
              width: 18, height: 18, borderRadius: 4,
              background: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)',
              border: sel ? 'none' : '1.5px solid rgba(255,255,255,0.5)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '0.625rem', color: '#fff', fontWeight: 700,
            }}>
              {sel ? '✓' : ''}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// ClusterPanel — one cluster with expandable photo grid + actions
// ─────────────────────────────────────────────────────────────────────────────

function ClusterPanel({
  c,
  members,
  thumbById,
  deploymentId,
}: {
  c: ClusterAssignment
  members: string[]
  thumbById: Record<string, string | null>
  deploymentId: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmName, setConfirmName] = useState(c.scientific_name || '')
  const [reassignName, setReassignName] = useState('')
  const [mode, setMode] = useState<'idle' | 'reassign' | 'inat' | 'expert'>('idle')
  const [toast, setToast] = useState<string | null>(null)

  const confirmAll = useConfirmCluster(deploymentId)
  const reassign = useBulkReassign(deploymentId)
  const publish = useInatPublish()

  const bucket = c.review_depth === 'bulk' ? 'high' : c.review_depth === 'sample' ? 'medium' : c.review_depth === 'full' ? 'low' : null

  const toggleOne = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(id)) { next.delete(id) } else { next.add(id) }
    return next
  })

  const selectAll = () => setSelected(new Set(members))
  const selectNone = () => setSelected(new Set())

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3500)
  }

  const handleConfirmAll = () => {
    if (!confirmName.trim()) return
    confirmAll.mutate(
      { id: c.id, taxon: { scientific_name: confirmName.trim() } },
      { onSuccess: () => showToast(`Cluster ${c.cluster_id} confirmed as ${confirmName.trim()}`) }
    )
  }

  const handleReassign = () => {
    if (!reassignName.trim() || selected.size === 0) return
    reassign.mutate(
      { media_ids: [...selected], scientific_name: reassignName.trim(), decision: 'reassign' },
      {
        onSuccess: ({ ok, failed }) => {
          showToast(`Reassigned ${ok} image${ok !== 1 ? 's' : ''} to "${reassignName.trim()}"${failed > 0 ? ` (${failed} failed)` : ''}`)
          setMode('idle')
          setSelected(new Set())
        },
      }
    )
  }

  const handleExpert = () => {
    if (selected.size === 0) return
    reassign.mutate(
      { media_ids: [...selected], scientific_name: '', decision: 'expert' },
      {
        onSuccess: ({ ok }) => {
          showToast(`${ok} image${ok !== 1 ? 's' : ''} sent to expert queue`)
          setMode('idle')
          setSelected(new Set())
        },
      }
    )
  }

  const handleInatPublish = () => {
    if (selected.size === 0 || !confirmName.trim()) return
    publish.mutate(
      { media_ids: [...selected], scientific_name: confirmName.trim() },
      {
        onSuccess: ({ submitted }) => {
          showToast(`Published ${submitted} observation${submitted !== 1 ? 's' : ''} to iNaturalist`)
          setMode('idle')
          setSelected(new Set())
        },
        onError: (err: unknown) => showToast(`iNat publish failed: ${err instanceof Error ? err.message : String(err)}`),
      }
    )
  }

  return (
    <div
      className="glass-card"
      style={{ padding: '1rem 1.25rem', opacity: c.is_outlier_cluster ? 0.75 : 1 }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Contact strip (always visible, max 5 thumbs) */}
        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
          {members.slice(0, 5).map(mid => (
            <Thumb key={mid} src={thumbById[mid]}
              style={{ width: 52, height: 40, objectFit: 'cover', borderRadius: 3, background: 'var(--surface)' }} />
          ))}
          {members.length > 5 && (
            <div style={{
              width: 52, height: 40, borderRadius: 3, background: 'rgba(255,255,255,0.06)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '0.7rem', opacity: 0.7,
            }}>
              +{members.length - 5}
            </div>
          )}
          {members.length === 0 && (
            <div style={{ width: 52, height: 40, borderRadius: 3, background: 'var(--surface)', border: '1px dashed var(--border)' }} />
          )}
        </div>

        {/* Meta */}
        <div style={{ flex: 1, minWidth: 160 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <strong>{c.is_outlier_cluster ? '⚠ Outliers' : `Cluster ${c.cluster_id}`}</strong>
            <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{c.image_count} images</span>
            {bucket && (
              <span style={{ fontSize: '0.68rem', padding: '1px 7px', borderRadius: 10, background: PURITY_COLOR[bucket], color: '#fff' }}>
                {DEPTH_LABEL[c.review_depth || 'full']}
              </span>
            )}
            {c.review_state === 'confirmed' && (
              <span style={{ fontSize: '0.7rem', color: '#4caf50' }}>✓ {c.scientific_name}</span>
            )}
          </div>
          {c.mean_confidence != null && (
            <span style={{ fontSize: '0.7rem', opacity: 0.55 }}>
              mean conf {Math.round(c.mean_confidence * 100)}%
            </span>
          )}
        </div>

        {/* Expand toggle */}
        <button
          onClick={() => { setExpanded(v => !v); setMode('idle') }}
          style={{
            fontSize: '0.75rem', padding: '0.3rem 0.75rem',
            border: '1px solid var(--border)', borderRadius: 'var(--radius)',
            background: 'transparent', color: 'var(--primary)', cursor: 'pointer',
          }}
        >
          {expanded ? '▲ Collapse' : '▼ Expand'}
        </button>
      </div>

      {/* Toast */}
      {toast && (
        <div style={{
          marginTop: '0.6rem', padding: '0.4rem 0.75rem', borderRadius: 'var(--radius)',
          background: 'rgba(76,175,80,0.15)', border: '1px solid rgba(76,175,80,0.3)',
          fontSize: '0.8125rem', color: '#4caf50',
        }}>
          ✓ {toast}
        </div>
      )}

      {/* Expanded section */}
      {expanded && (
        <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
          {/* Selection toolbar */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '0.25rem' }}>
            <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>
              {selected.size} / {members.length} selected
            </span>
            <button onClick={selectAll} style={{ fontSize: '0.7rem', padding: '0.2rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
              All
            </button>
            <button onClick={selectNone} style={{ fontSize: '0.7rem', padding: '0.2rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
              None
            </button>

            {/* Action mode buttons (only when items are selected) */}
            {selected.size > 0 && (
              <>
                <button
                  onClick={() => setMode(m => m === 'reassign' ? 'idle' : 'reassign')}
                  style={{
                    fontSize: '0.7rem', padding: '0.2rem 0.6rem',
                    border: `1px solid ${mode === 'reassign' ? 'var(--primary)' : 'var(--border)'}`,
                    borderRadius: 'var(--radius)', background: mode === 'reassign' ? 'rgba(76,175,80,0.12)' : 'transparent',
                    color: mode === 'reassign' ? 'var(--primary)' : 'var(--text-color)', cursor: 'pointer',
                  }}
                >
                  🏷 Reassign
                </button>
                <button
                  onClick={() => setMode(m => m === 'inat' ? 'idle' : 'inat')}
                  style={{
                    fontSize: '0.7rem', padding: '0.2rem 0.6rem',
                    border: `1px solid ${mode === 'inat' ? '#74ac00' : 'var(--border)'}`,
                    borderRadius: 'var(--radius)', background: mode === 'inat' ? 'rgba(116,172,0,0.12)' : 'transparent',
                    color: mode === 'inat' ? '#74ac00' : 'var(--text-color)', cursor: 'pointer',
                  }}
                >
                  🌿 Publish to iNat
                </button>
                <button
                  onClick={() => setMode(m => m === 'expert' ? 'idle' : 'expert')}
                  style={{
                    fontSize: '0.7rem', padding: '0.2rem 0.6rem',
                    border: `1px solid ${mode === 'expert' ? '#f59e0b' : 'var(--border)'}`,
                    borderRadius: 'var(--radius)', background: mode === 'expert' ? 'rgba(245,158,11,0.1)' : 'transparent',
                    color: mode === 'expert' ? '#f59e0b' : 'var(--text-color)', cursor: 'pointer',
                  }}
                >
                  🔬 Flag for expert
                </button>
              </>
            )}
          </div>

          {/* Photo grid */}
          <PhotoGrid mediaIds={members} thumbById={thumbById} selected={selected} onToggle={toggleOne} />

          {/* Action panels */}
          {mode === 'reassign' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'rgba(76,175,80,0.06)', borderRadius: 'var(--radius)', border: '1px solid rgba(76,175,80,0.2)' }}>
              <span style={{ fontSize: '0.75rem', opacity: 0.7 }}>Reassign {selected.size} image{selected.size !== 1 ? 's' : ''} to:</span>
              <input
                type="text"
                placeholder="Scientific name…"
                value={reassignName}
                onChange={e => setReassignName(e.target.value)}
                style={{ flex: 1, minWidth: 180, padding: '0.375rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem' }}
              />
              <button
                className="btn"
                disabled={!reassignName.trim() || reassign.isPending}
                onClick={handleReassign}
                style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
              >
                {reassign.isPending ? '…' : 'Apply'}
              </button>
              <button onClick={() => setMode('idle')} style={{ fontSize: '0.75rem', padding: '0.375rem 0.6rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          )}

          {mode === 'inat' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'rgba(116,172,0,0.06)', borderRadius: 'var(--radius)', border: '1px solid rgba(116,172,0,0.25)' }}>
              <span style={{ fontSize: '0.75rem', opacity: 0.7 }}>Publish {selected.size} image{selected.size !== 1 ? 's' : ''} as:</span>
              <input
                type="text"
                placeholder="Scientific name…"
                value={confirmName}
                onChange={e => setConfirmName(e.target.value)}
                style={{ flex: 1, minWidth: 180, padding: '0.375rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem' }}
              />
              <button
                className="btn"
                disabled={!confirmName.trim() || publish.isPending}
                onClick={handleInatPublish}
                style={{ fontSize: '0.8125rem', background: 'linear-gradient(135deg,#74ac00,#4a7c00)', border: 'none' }}
              >
                {publish.isPending ? '…' : '🌿 Publish'}
              </button>
              <button onClick={() => setMode('idle')} style={{ fontSize: '0.75rem', padding: '0.375rem 0.6rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          )}

          {mode === 'expert' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'rgba(245,158,11,0.06)', borderRadius: 'var(--radius)', border: '1px solid rgba(245,158,11,0.25)' }}>
              <span style={{ fontSize: '0.8125rem', opacity: 0.8 }}>
                Flag {selected.size} image{selected.size !== 1 ? 's' : ''} as needing expert review?
              </span>
              <button
                className="btn"
                disabled={reassign.isPending}
                onClick={handleExpert}
                style={{ fontSize: '0.8125rem', background: '#f59e0b', border: 'none', color: '#fff' }}
              >
                {reassign.isPending ? '…' : '🔬 Send to expert queue'}
              </button>
              <button onClick={() => setMode('idle')} style={{ fontSize: '0.75rem', padding: '0.375rem 0.6rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--text-color)', cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          )}

          {/* Confirm-all strip (only for unconfirmed non-outlier clusters) */}
          {!c.is_outlier_cluster && c.review_state !== 'confirmed' && mode === 'idle' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
              <span style={{ fontSize: '0.75rem', opacity: 0.7 }}>Confirm entire cluster as:</span>
              <input
                type="text"
                placeholder="Scientific name…"
                value={confirmName}
                onChange={e => setConfirmName(e.target.value)}
                style={{ flex: 1, minWidth: 180, padding: '0.375rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem' }}
              />
              <button
                className="btn"
                disabled={!confirmName.trim() || confirmAll.isPending}
                onClick={handleConfirmAll}
                style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
              >
                {confirmAll.isPending ? '…' : 'Confirm all'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Collapsed confirm strip (quick path for small clusters) */}
      {!expanded && !c.is_outlier_cluster && c.review_state !== 'confirmed' && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.75rem' }}>
          <input
            type="text"
            placeholder="Scientific name…"
            value={confirmName}
            onChange={e => setConfirmName(e.target.value)}
            style={{ flex: 1, minWidth: 160, padding: '0.35rem 0.6rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem' }}
          />
          <button
            className="btn"
            disabled={!confirmName.trim() || confirmAll.isPending}
            onClick={handleConfirmAll}
            style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
          >
            {confirmAll.isPending ? '…' : 'Confirm'}
          </button>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page root
// ─────────────────────────────────────────────────────────────────────────────

export function ClusterReviewPage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const clustersQ = useClusters(deployment_id)
  const umapQ = useUmapCoords(deployment_id)
  const registryQ = useMediaRegistry(deployment_id)
  const embed = useEmbedDeployment()

  const thumbById = useMemo(() => {
    const m: Record<string, string | null> = {}
    for (const x of registryQ.data?.media || []) m[x.id] = x.thumbnail_url
    return m
  }, [registryQ.data])

  // Build cluster → member list from UMAP points (already fetched for thumbnails)
  const membersByCluster = useMemo(() => {
    const m: Record<number, string[]> = {}
    for (const p of umapQ.data?.points || []) {
      if (!m[p.cluster_id]) m[p.cluster_id] = []
      m[p.cluster_id].push(p.media_id)
    }
    return m
  }, [umapQ.data])

  const clusters = clustersQ.data?.clusters || []
  const confirmed = clusters.filter(c => c.review_state === 'confirmed').length
  const total = clusters.filter(c => !c.is_outlier_cluster).length

  if (clustersQ.isLoading) return <div style={{ padding: '2rem' }}>Loading clusters…</div>

  if (!clustersQ.data?.embedding_run_id) {
    return (
      <div className="glass-card" style={{ padding: '2rem', textAlign: 'center', maxWidth: 560, margin: '2rem auto' }}>
        <h3 style={{ marginTop: 0 }}>No Wildlife Brain run yet</h3>
        <p style={{ opacity: 0.8, fontSize: '0.9rem' }}>
          Run DINOv3 embedding + clustering on this deployment to generate species clusters for review.
        </p>
        <button
          className="btn"
          disabled={embed.isPending || !deployment_id}
          onClick={() => deployment_id && embed.mutate(deployment_id)}
          style={{ backgroundColor: 'var(--primary)' }}
        >
          {embed.isPending ? 'Queuing…' : '🧠 Run Wildlife Brain'}
        </button>
        {embed.isSuccess && embed.data?.job_id && (
          <p style={{ color: '#4caf50', fontSize: '0.85rem' }}>
            Queued (job {embed.data.job_id.slice(0, 8)}). Refresh shortly.
          </p>
        )}
        {embed.isError && (
          <p style={{ color: '#f44336', fontSize: '0.85rem' }}>
            ⚠ {embed.error instanceof Error ? embed.error.message : 'Failed to start Wildlife Brain.'}
          </p>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', color: 'var(--text-color)' }}>
      {/* Header */}
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h3 style={{ margin: 0 }}>Cluster Review</h3>
          <span style={{ fontSize: '0.8125rem', opacity: 0.7 }}>
            {total} clusters · {confirmed}/{total} confirmed
            {clusters.some(c => c.is_outlier_cluster) && ` · outlier cluster`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <Link to={`/review/${deployment_id}`} className="btn btn-outline"
            style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>
            ▶ Review Queue
          </Link>
          <Link to={`/umap/${deployment_id}`} className="btn btn-outline"
            style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>
            ✦ UMAP
          </Link>
          <Link to={`/annotations?deployment=${deployment_id}`} className="btn btn-outline"
            style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }}>
            🏷️ Annotations
          </Link>
        </div>
      </div>

      {/* Tip (once there are unconfirmed clusters) */}
      {total > confirmed && (
        <div style={{ fontSize: '0.8rem', opacity: 0.6, padding: '0 0.25rem' }}>
          💡 Expand a cluster to select individual images — then reassign, publish to iNaturalist, or flag for expert review.
        </div>
      )}

      {/* Cluster panels */}
      {clusters.map(c => (
        <ClusterPanel
          key={c.id}
          c={c}
          members={membersByCluster[c.cluster_id] || []}
          thumbById={thumbById}
          deploymentId={deployment_id!}
        />
      ))}

      {clusters.length === 0 && (
        <div style={{ textAlign: 'center', opacity: 0.5, padding: '3rem' }}>No clusters found for this run.</div>
      )}
    </div>
  )
}
