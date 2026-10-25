import { useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Camera, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Upload } from 'lucide-react'
import { useClusters, useUmapCoords, useConfirmCluster, useEmbedDeployment, useBulkReview } from '../hooks/useBrain'
import type { ClusterAssignment } from '../hooks/useBrain'
import { useMediaThumbs } from '../hooks/useMediaThumbs'
import { useDeploymentProject } from '../hooks/useDeploymentProject'
import { useProjectRoles } from '../hooks/useProjectRoles'
import { useINat } from '../hooks/useINat'
import { SpeciesPicker, type SpeciesSelection } from '../components/data/SpeciesPicker'
import { FeatureOff } from '../components/common/FeatureOff'
import { isFeatureDisabled } from '../lib/featureFlags'
import { CLUSTERS_PER_PAGE, MEMBERS_PER_STEP, orderClusters, pageOf } from '../lib/clusterReview'
import { inatBlocker, inatErrorNotice, inatPublishNotice, type CardNotice } from '../lib/inatPublish'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const PURITY_COLOR: Record<string, string> = { high: '#4caf50', medium: '#ff9800', low: '#f44336' }
const DEPTH_LABEL: Record<string, string> = { bulk: 'Bulk confirm', sample: 'Sample review', full: 'Full review' }

const SMALL_BTN: React.CSSProperties = {
  fontSize: '0.75rem', padding: '0.25rem 0.6rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', gap: 4,
}
const LINK_BTN: React.CSSProperties = { backgroundColor: 'transparent', border: '1px solid var(--border)', textDecoration: 'none', fontSize: '0.8125rem' }
const MUTED: React.CSSProperties = { fontSize: '0.75rem', color: 'var(--text-muted)' }

const photos = (n: number) => `${n} photo${n === 1 ? '' : 's'}`
const speciesName = (s: SpeciesSelection) => s.vernacular_name || s.scientific_name
const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

/** The last action's result in a card: kept until the next one, errors included. */
type Notice = CardNotice | null
type Mode = 'idle' | 'reassign' | 'expert' | 'inat'

// ─────────────────────────────────────────────────────────────────────────────
// Thumb: a photo's thumbnail, or the grid's no-thumbnail placeholder when there
// is none to show (no rendition yet and a private original, #305)
// ─────────────────────────────────────────────────────────────────────────────

function Thumb({ src, style }: { src: string | null | undefined; style: React.CSSProperties }) {
  if (!src) {
    return (
      <div style={{ ...style, background: 'rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Camera size={14} color="var(--text-muted)" aria-hidden />
      </div>
    )
  }
  return <img src={src} alt="" loading="lazy" style={style} />
}

const NOTICE_LINK: React.CSSProperties = { fontSize: '0.8125rem', color: 'var(--primary)' }

/** A notice's link: a route of this site, or an iNaturalist page in a new tab. */
function NoticeLinkTo({ link }: { link: NonNullable<CardNotice['link']> }) {
  return link.external
    ? <a href={link.to} target="_blank" rel="noreferrer" style={NOTICE_LINK}>{link.label}</a>
    : <Link to={link.to} style={NOTICE_LINK}>{link.label}</Link>
}

// ─────────────────────────────────────────────────────────────────────────────
// PhotoGrid: an expanded cluster's photos, selectable for members
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
  /** Absent for a viewer: the photos are shown, not selectable. */
  onToggle?: (id: string) => void
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
            onClick={onToggle ? () => onToggle(mid) : undefined}
            style={{
              position: 'relative', cursor: onToggle ? 'pointer' : 'default',
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
            {onToggle && (
              <div style={{
                position: 'absolute', top: 3, left: 3,
                width: 18, height: 18, borderRadius: 4,
                background: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)',
                border: sel ? 'none' : '1.5px solid rgba(255,255,255,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff',
              }}>
                {sel && <Check size={12} strokeWidth={3} aria-hidden />}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// ClusterPanel: one cluster with its confirm strip, expandable photos and actions
// ─────────────────────────────────────────────────────────────────────────────

function ClusterPanel({
  c,
  members,
  stripThumbs,
  deploymentId,
  canWrite,
  inat,
  hasLocation,
  onConfirmed,
}: {
  c: ClusterAssignment
  members: string[]
  stripThumbs: Record<string, string | null>
  deploymentId: string
  /** project_member or above on the deployment's project: may confirm, reassign, flag and publish. */
  canWrite: boolean
  inat: ReturnType<typeof useINat>
  /** The deployment has coordinates, which iNaturalist needs; undefined while unknown. */
  hasLocation?: boolean
  onConfirmed: (clusterAssignmentId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [shown, setShown] = useState(MEMBERS_PER_STEP)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmPick, setConfirmPick] = useState<SpeciesSelection | null>(null)
  const [reassignPick, setReassignPick] = useState<SpeciesSelection | null>(null)
  const [mode, setMode] = useState<Mode>('idle')
  const [notice, setNotice] = useState<Notice>(null)

  const confirm = useConfirmCluster(deploymentId)
  const review = useBulkReview(deploymentId)
  // No query cache holds iNaturalist state, so publishing is a plain call, not a mutation.
  const [publishing, setPublishing] = useState(false)
  // Offered once the status check has answered and iNaturalist is switched on.
  const canPublish = canWrite && inat.enabled && inat.connected !== null
  const publishBlocker = inatBlocker({ connected: inat.connected, hasLocation, selected: selected.size })
  const visible = expanded ? members.slice(0, shown) : []
  const gridThumbs = useMediaThumbs(visible)

  const bucket = c.review_depth === 'bulk' ? 'high' : c.review_depth === 'sample' ? 'medium' : c.review_depth === 'full' ? 'low' : null
  const canConfirm = canWrite && !c.is_outlier_cluster && c.review_state !== 'confirmed'

  const toggleOne = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(id)) { next.delete(id) } else { next.add(id) }
    return next
  })

  const handleConfirm = () => {
    const pick = confirmPick
    if (!pick) return
    setNotice(null)
    confirm.mutate(
      {
        id: c.id,
        taxon: {
          taxon_id: pick.taxon_id ?? undefined,
          scientific_name: pick.scientific_name,
          vernacular_name: pick.vernacular_name ?? undefined,
        },
      },
      {
        onSuccess: (r) => {
          onConfirmed(c.id)
          setNotice({ ok: true, text: `Confirmed ${photos(r?.observations_created ?? c.image_count)} as ${speciesName(pick)}.` })
        },
        onError: (err) => setNotice({ ok: false, text: `Not confirmed: ${errorText(err)}` }),
      },
    )
  }

  const runReview = (decision: 'reassign' | 'expert') => {
    const pick = reassignPick
    if (selected.size === 0 || (decision === 'reassign' && !pick)) return
    setNotice(null)
    review.mutate(
      { mediaIds: [...selected], decision, taxon: decision === 'reassign' && pick ? pick : undefined },
      {
        onSuccess: ({ ok, failed }) => {
          const done = decision === 'reassign' && pick
            ? `Reassigned ${photos(ok)} to ${speciesName(pick)}`
            : `Flagged ${photos(ok)} for an expert`
          setNotice({ ok: failed === 0, text: failed > 0 ? `${done}; ${failed} failed.` : `${done}.` })
          setMode('idle')
          setSelected(new Set())
        },
        onError: (err) => setNotice({ ok: false, text: `Not saved: ${errorText(err)}` }),
      },
    )
  }

  const runPublish = async () => {
    if (selected.size === 0 || publishBlocker || publishing) return
    setNotice(null)
    setPublishing(true)
    try {
      setNotice(inatPublishNotice(await inat.publish([...selected])))
      setMode('idle')
      setSelected(new Set())
    } catch (err) {
      setNotice(inatErrorNotice(err))
    } finally {
      setPublishing(false)
    }
  }

  const modeButton = (m: Exclude<Mode, 'idle'>, label: React.ReactNode) => (
    <button
      onClick={() => setMode(cur => (cur === m ? 'idle' : m))}
      style={{ ...SMALL_BTN, borderColor: mode === m ? 'var(--primary)' : 'var(--border)', color: mode === m ? 'var(--primary)' : 'var(--text-color)' }}
    >
      {label}
    </button>
  )

  return (
    <div className="card" style={{ padding: '1rem 1.25rem' }}>
      {/* Header row */}
      <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Contact strip (always visible, max 5 thumbs) */}
        <div style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
          {members.slice(0, 5).map(mid => (
            <Thumb key={mid} src={stripThumbs[mid]}
              style={{ width: 52, height: 40, objectFit: 'cover', borderRadius: 3, background: 'var(--surface)' }} />
          ))}
          {members.length > 5 && (
            <div style={{
              width: 52, height: 40, borderRadius: 3, background: 'rgba(255,255,255,0.06)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', ...MUTED,
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
            <strong>{c.is_outlier_cluster ? 'Outliers' : `Cluster ${c.cluster_id}`}</strong>
            <span style={MUTED}>{photos(c.image_count)}</span>
            {bucket && (
              <span style={{ fontSize: '0.68rem', padding: '1px 7px', borderRadius: 10, background: PURITY_COLOR[bucket], color: '#fff' }}>
                {DEPTH_LABEL[c.review_depth || 'full']}
              </span>
            )}
            {c.review_state === 'confirmed' && (
              <span style={{ fontSize: '0.75rem', color: 'var(--primary)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                <Check size={12} aria-hidden /> {c.scientific_name}
              </span>
            )}
          </div>
          {c.mean_confidence != null && (
            <span style={MUTED}>mean conf {Math.round(c.mean_confidence * 100)}%</span>
          )}
        </div>

        {/* Expand toggle */}
        <button
          onClick={() => { setExpanded(v => !v); setMode('idle') }}
          style={{ ...SMALL_BTN, color: 'var(--primary)' }}
        >
          {expanded ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
          {expanded ? 'Hide photos' : 'Show photos'}
        </button>
      </div>

      {/* Confirm the whole cluster (members only, unconfirmed non-outlier clusters) */}
      {canConfirm && mode === 'idle' && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.75rem' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <SpeciesPicker placeholder="Confirm the cluster as..." disabled={confirm.isPending} onSelect={setConfirmPick} />
          </div>
          <button
            className="btn"
            disabled={!confirmPick || confirm.isPending}
            onClick={handleConfirm}
            style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
          >
            {confirm.isPending ? 'Confirming…' : confirmPick ? `Confirm as ${speciesName(confirmPick)}` : 'Confirm'}
          </button>
        </div>
      )}

      {notice && (
        <div role="status" style={{ marginTop: '0.6rem', fontSize: '0.8125rem', color: notice.ok ? 'var(--primary)' : 'var(--error)', display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
          {notice.ok && <Check size={14} aria-hidden />}
          {notice.text}
          {notice.link && <NoticeLinkTo link={notice.link} />}
        </div>
      )}

      {/* Expanded section */}
      {expanded && (
        <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
          {/* Selection toolbar (members only) */}
          {canWrite && (
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '0.25rem' }}>
              <span style={MUTED}>{selected.size} of {members.length} selected</span>
              <button onClick={() => setSelected(new Set(members))} style={SMALL_BTN}>Select all</button>
              <button onClick={() => setSelected(new Set())} style={SMALL_BTN}>Clear</button>
              {selected.size > 0 && modeButton('reassign', 'Reassign')}
              {selected.size > 0 && modeButton('expert', 'Flag for expert')}
              {selected.size > 0 && canPublish && modeButton('inat', <><Upload size={12} aria-hidden /> Publish to iNaturalist</>)}
            </div>
          )}

          <PhotoGrid mediaIds={visible} thumbById={gridThumbs} selected={selected} onToggle={canWrite ? toggleOne : undefined} />
          {members.length > shown && (
            <button onClick={() => setShown(n => n + MEMBERS_PER_STEP)} style={{ ...SMALL_BTN, marginTop: '0.5rem' }}>
              Show {Math.min(MEMBERS_PER_STEP, members.length - shown)} more
            </button>
          )}

          {/* Action panels */}
          {mode === 'reassign' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'rgba(76,175,80,0.06)', borderRadius: 'var(--radius)', border: '1px solid rgba(76,175,80,0.2)' }}>
              <span style={MUTED}>Reassign {photos(selected.size)} to:</span>
              <div style={{ flex: 1, minWidth: 200 }}>
                <SpeciesPicker autoFocus disabled={review.isPending} onSelect={setReassignPick} />
              </div>
              <button
                className="btn"
                disabled={!reassignPick || review.isPending}
                onClick={() => runReview('reassign')}
                style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
              >
                {review.isPending ? 'Saving…' : 'Apply'}
              </button>
              <button onClick={() => setMode('idle')} style={SMALL_BTN}>Cancel</button>
            </div>
          )}

          {mode === 'expert' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'rgba(245,158,11,0.06)', borderRadius: 'var(--radius)', border: '1px solid rgba(245,158,11,0.25)' }}>
              <span style={{ fontSize: '0.8125rem' }}>Flag {photos(selected.size)} as needing expert review?</span>
              <button
                className="btn"
                disabled={review.isPending}
                onClick={() => runReview('expert')}
                style={{ fontSize: '0.8125rem', background: '#f59e0b', border: 'none', color: '#fff' }}
              >
                {review.isPending ? 'Saving…' : 'Flag for expert'}
              </button>
              <button onClick={() => setMode('idle')} style={SMALL_BTN}>Cancel</button>
            </div>
          )}

          {mode === 'inat' && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.75rem', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
              {publishBlocker ? (
                <>
                  <span style={{ fontSize: '0.8125rem' }}>{publishBlocker.text}</span>
                  {publishBlocker.link && <NoticeLinkTo link={publishBlocker.link} />}
                </>
              ) : (
                <>
                  <span style={{ fontSize: '0.8125rem' }}>
                    Publish {photos(selected.size)}: one observation per encounter, named by the photos' labels (yours before the AI's), location obscured.
                  </span>
                  <button
                    className="btn"
                    disabled={publishing || selected.size === 0}
                    onClick={runPublish}
                    style={{ fontSize: '0.8125rem', backgroundColor: 'var(--primary)' }}
                  >
                    {publishing ? 'Publishing…' : 'Publish'}
                  </button>
                </>
              )}
              <button onClick={() => setMode('idle')} style={SMALL_BTN}>Cancel</button>
            </div>
          )}
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
  const projectQ = useDeploymentProject(deployment_id)
  const roles = useProjectRoles()
  // One iNaturalist status check for the page, not one per cluster.
  const inat = useINat()
  const embed = useEmbedDeployment()
  const [showConfirmed, setShowConfirmed] = useState(false)
  const [page, setPage] = useState(0)
  // Clusters confirmed during this visit stay in place, showing their result.
  const [confirmedHere, setConfirmedHere] = useState<Set<string>>(new Set())

  const projectId = projectQ.data?.projectId
  const canWrite = !!projectId && !roles.loading && roles.canWrite(projectId)
  const photosUrl = `/annotations?deployment=${deployment_id}`

  // Build cluster → member list from UMAP points
  const membersByCluster = useMemo(() => {
    const m: Record<number, string[]> = {}
    for (const p of umapQ.data?.points || []) {
      if (!m[p.cluster_id]) m[p.cluster_id] = []
      m[p.cluster_id].push(p.media_id)
    }
    return m
  }, [umapQ.data])

  const clusters = useMemo(() => clustersQ.data?.clusters ?? [], [clustersQ.data])
  const ordered = useMemo(() => orderClusters(clusters, showConfirmed, confirmedHere), [clusters, showConfirmed, confirmedHere])
  const shownPage = pageOf(ordered, page, CLUSTERS_PER_PAGE)
  const stripThumbs = useMediaThumbs(shownPage.items.flatMap(c => (membersByCluster[c.cluster_id] || []).slice(0, 5)))

  const confirmed = clusters.filter(c => c.review_state === 'confirmed').length
  const total = clusters.filter(c => !c.is_outlier_cluster).length

  const goToPage = (p: number) => {
    setPage(p)
    window.scrollTo({ top: 0 })
  }

  if (clustersQ.isLoading) return <div style={{ padding: '2rem' }}>Loading clusters…</div>
  if (isFeatureDisabled(clustersQ.error)) return <FeatureOff backTo={photosUrl} backLabel="Back to the photos" />
  if (clustersQ.error) {
    return <div style={{ padding: '2rem', color: 'var(--error)' }}>Could not load clusters: {errorText(clustersQ.error)}</div>
  }

  if (!clustersQ.data?.embedding_run_id) {
    return (
      <div className="card" style={{ padding: '2rem', textAlign: 'center', maxWidth: 560, margin: '2rem auto' }}>
        <h3 style={{ marginTop: 0 }}>No Wildlife Brain run yet</h3>
        {canWrite ? (
          <>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>
              Run DINOv3 embedding and clustering on this deployment to group its photos for review.
            </p>
            <button
              className="btn"
              disabled={embed.isPending || !deployment_id}
              onClick={() => deployment_id && embed.mutate(deployment_id)}
              style={{ backgroundColor: 'var(--primary)' }}
            >
              {embed.isPending ? 'Queuing…' : 'Run Wildlife Brain'}
            </button>
          </>
        ) : (
          <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>This deployment has no clusters yet.</p>
        )}
        {embed.isSuccess && embed.data?.job_id && (
          <p style={{ color: 'var(--primary)', fontSize: '0.85rem' }}>
            Queued (job {embed.data.job_id.slice(0, 8)}). Refresh shortly.
          </p>
        )}
        {embed.isError && (
          <p style={{ color: 'var(--error)', fontSize: '0.85rem' }}>
            {embed.error instanceof Error ? embed.error.message : 'Failed to start Wildlife Brain.'}
          </p>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', color: 'var(--text-color)' }}>
      {/* Header */}
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h3 style={{ margin: 0 }}>Cluster Review</h3>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
            {total} clusters · {confirmed}/{total} confirmed
            {clusters.some(c => c.is_outlier_cluster) && ` · outlier cluster`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {/* UMAP sits behind the same flag as this page, which has just answered. */}
          <Link to={`/umap/${deployment_id}`} className="btn btn-outline" style={LINK_BTN}>UMAP</Link>
          <Link to={photosUrl} className="btn btn-outline" style={LINK_BTN}>Photos</Link>
        </div>
      </div>

      {/* One line of help, and the confirmed filter */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', padding: '0 0.25rem' }}>
        <span style={MUTED}>
          {showConfirmed ? 'Unconfirmed clusters first, biggest first.' : 'Unconfirmed clusters, biggest first.'}
        </span>
        {confirmed > 0 && (
          <button onClick={() => { setShowConfirmed(v => !v); setPage(0) }} style={SMALL_BTN}>
            {showConfirmed ? 'Hide confirmed' : `Show confirmed (${confirmed})`}
          </button>
        )}
      </div>

      {/* Cluster panels, one page at a time */}
      {shownPage.items.map(c => (
        <ClusterPanel
          key={c.id}
          c={c}
          members={membersByCluster[c.cluster_id] || []}
          stripThumbs={stripThumbs}
          deploymentId={deployment_id!}
          canWrite={canWrite}
          inat={inat}
          hasLocation={projectQ.data?.hasLocation}
          onConfirmed={id => setConfirmedHere(prev => new Set(prev).add(id))}
        />
      ))}

      {clusters.length === 0 && (
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '3rem' }}>No clusters found for this run.</div>
      )}
      {clusters.length > 0 && ordered.length === 0 && (
        <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '3rem' }}>Every cluster is confirmed.</div>
      )}

      {/* Pager */}
      {shownPage.pages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '0.75rem' }}>
          <button disabled={shownPage.page === 0} onClick={() => goToPage(shownPage.page - 1)} style={SMALL_BTN}>
            <ChevronLeft size={14} aria-hidden /> Previous
          </button>
          <span style={MUTED}>{shownPage.first} to {shownPage.last} of {ordered.length}</span>
          <button disabled={shownPage.page >= shownPage.pages - 1} onClick={() => goToPage(shownPage.page + 1)} style={SMALL_BTN}>
            Next <ChevronRight size={14} aria-hidden />
          </button>
        </div>
      )}
    </div>
  )
}
