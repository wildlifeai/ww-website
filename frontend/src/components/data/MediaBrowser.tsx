 
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { survivingPending } from '../../lib/pendingCards'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { useINat } from '../../hooks/useINat'
import { INatBadge, type INatState } from './INatBadge'
import { MediaDetail } from './MediaDetail'
import { FilterSelect } from '../ui/ControlBar'
import { MultiSelect } from '../ui/MultiSelect'
import { Ribbon } from '../ui/Ribbon'
import { StatusBadge, deriveAnnotationStatus } from '../ui/StatusBadge'
import type { AnnotationStatus } from '../ui/StatusBadge'
import { Modal } from '../ui/Modal'
import { isHumanReviewed, isAiLabel, humanCreateFields } from '../../lib/observations'
import { getLocalPreview } from '../../lib/localPreviewStore'
import { apiClient } from '../../lib/apiClient'
import { showUndoToast } from '../common/undoToastBus'
import { BulkLabelModal } from './BulkLabelModal'
import { type SpeciesSelection } from './SpeciesPicker'
import { getTimeOfDay, formatCaptureTime } from '../../lib/time'
import { useQueryClient } from '@tanstack/react-query'
import { MediaGroup } from './MediaGroup'
import { useMultiClusters, useConfirmCluster, useSimilarImages } from '../../hooks/useBrain'
import { useUploadStore } from '../../contexts/UploadContext'
import { useJobsList } from '../../hooks/useJobs'
import { MediaBulkActions, type BulkAction } from './MediaBulkActions'
import { DeleteConfirmModal, AiModelPickerModal, PipelineLogModal } from './BulkActionModals'
import { TrainModelModal } from './TrainModelModal'

// ─────────────────────────────────────────────────────────────────────────────
// ClusterLabelAll — per-cluster header action shown when grouping by cluster.
// Labels every image in the cluster as one species in a single call (the bulk
// "confirm cluster" workflow, brought in from the old standalone Explore page).
// ─────────────────────────────────────────────────────────────────────────────

function ClusterLabelAll({ clusterId, deploymentId, onDone }: { clusterId: string; deploymentId?: string; onDone: (created: number, name: string) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const confirm = useConfirmCluster(deploymentId)

  const apply = () => {
    const sci = name.trim()
    if (!sci) return
    confirm.mutate(
      { id: clusterId, taxon: { scientific_name: sci } },
      { onSuccess: (r) => { setOpen(false); setName(''); onDone(r?.observations_created ?? 0, sci) } },
    )
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Label every image in this cluster as one species"
        style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', color: 'var(--primary)', cursor: 'pointer', whiteSpace: 'nowrap' }}
      >
        ✓ Label all…
      </button>
    )
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
      <input
        autoFocus
        value={name}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') apply(); else if (e.key === 'Escape') { setOpen(false); setName('') } }}
        placeholder="scientific name"
        style={{ fontSize: '0.72rem', padding: '0.2rem 0.4rem', width: 160, border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)' }}
      />
      <button
        type="button"
        onClick={apply}
        disabled={!name.trim() || confirm.isPending}
        style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', border: 'none', borderRadius: 'var(--radius)', background: 'var(--primary)', color: '#fff', cursor: 'pointer', opacity: !name.trim() || confirm.isPending ? 0.5 : 1 }}
      >
        {confirm.isPending ? '…' : 'Apply'}
      </button>
      <button
        type="button"
        onClick={() => { setOpen(false); setName('') }}
        style={{ fontSize: '0.72rem', padding: '0.2rem 0.4rem', border: 'none', background: 'transparent', color: 'var(--text-color)', opacity: 0.6, cursor: 'pointer' }}
      >
        ✕
      </button>
      {confirm.isError && <span style={{ fontSize: '0.7rem', color: 'var(--error)' }}>failed</span>}
    </span>
  )
}

// Columns fetched for a media record (+ its assets and observations). Shared by
// the paginated grid loader and the "find similar" fetch so they stay in sync.
const MEDIA_SELECT =
  'id, deployment_id, file_path, file_name, file_mediatype, timestamp, created_at, file_public, media_comments, exif_metadata, ' +
  'media_assets(thumbnail_url, preview_url, animal_crop_url), ' +
  'observations(id, deployment_id, media_id, observation_type, scientific_name, vernacular_name, taxon_id, ' +
  'count, life_stage, sex, behavior, ' +
  'classification_method, classified_by, classification_probability, observation_comments, crop_url, ' +
  'review_status, source_type, ai_origin, source_model_version, reviewer_id, annotator_id, bbox_x, bbox_y, bbox_w, bbox_h)'

// ── Types ────────────────────────────────────────────────────────────────────

export interface MediaAssetRecord {
  thumbnail_url: string | null
  preview_url: string | null
  animal_crop_url: string | null
}

export interface MediaRecord {
  id: string
  deployment_id: string
  file_path: string
  file_name: string | null
  file_mediatype: string
  timestamp: string | null
  /** Row creation time; lets the grid retire an optimistic card once its real row has landed. */
  created_at?: string | null
  file_public: boolean
  media_comments: string | null
  exif_metadata: Record<string, unknown> | null
  // 1:1 rendition row (MEDIA_PREP): public Supabase Storage URLs for the grid.
  // PostgREST returns this to-one embed as a single object (media_id is the PK),
  // but tolerate an array too in case the relationship is ever re-detected.
  media_assets: MediaAssetRecord | MediaAssetRecord[] | null
  observations: ObservationRecord[]
  /** Optimistic placeholder for a still-uploading file (not a real DB row). Display-only. */
  _pending?: boolean
}

/** Normalise the media_assets embed (PostgREST returns a single object for to-one). */
function firstAsset(a: MediaAssetRecord | MediaAssetRecord[] | null | undefined): MediaAssetRecord | undefined {
  return Array.isArray(a) ? a[0] : (a ?? undefined)
}

export interface ObservationRecord {
  id: string
  deployment_id: string
  media_id: string | null
  observation_type: string | null
  scientific_name: string | null
  vernacular_name?: string | null
  taxon_id?: string | null
  count: number | null
  life_stage: string | null
  sex: string | null
  behavior: string | null
  classification_method: string | null
  classified_by: string | null
  classification_probability: number | null
  observation_comments: string | null
  crop_url?: string | null   // per-observation bbox crop (crop view)
  // AN-1/AN-2: validation provenance + lifecycle (authoritative for status)
  review_status?: string | null
  source_type?: string | null
  /** For source_type='ai': which AI layer produced the row — 'edge' (Camera AI) or 'cloud'. */
  ai_origin?: string | null
  source_model_version?: string | null
  reviewer_id?: string | null
  annotator_id?: string | null
  bbox_x?: number | null
  bbox_y?: number | null
  bbox_w?: number | null
  bbox_h?: number | null
}

interface Props {
  deployments: { id: string; location_name: string | null; project_id: string; timezone?: string | null }[]
  /** WS5-T6: Pre-select a deployment when navigating from the upload dock. */
  initialDeploymentId?: string
  /** Pre-apply a species filter (e.g. deep-linked from an Insights chart). */
  initialSpecies?: string
}

type TimeOfDay = 'all' | 'day' | 'night'
type GroupBy = 'none' | 'cluster' | 'species' | 'sex' | 'life_stage' | 'annotation_type' | 'deployment' | 'model' | 'annotator'

const GROUP_LABELS: Record<GroupBy, string> = {
  none: 'None', cluster: 'Cluster', species: 'Species', sex: 'Sex', life_stage: 'Life stage',
  annotation_type: 'Annotation type', deployment: 'Deployment', model: 'AI model', annotator: 'Annotator',
}

// A thin divider that separates the purpose-groups in the ribbon's first row
// (stats │ display controls │ selection).
const RIBBON_SECTION: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  borderLeft: '1px solid var(--border)', paddingLeft: '0.7rem', marginLeft: '0.7rem',
}

// Which observations get a card in the "Labels" view: AI detections that have a
// crop, plus any human-made or human-reviewed label (these usually have no crop,
// so they're shown on the full frame). AI detections with no crop are omitted.
function isLabelCard(o: ObservationRecord): boolean {
  return !!o.crop_url || o.source_type === 'human' || isHumanReviewed(o)
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Resolve a media record to a displayable image URL for the grid.
 *
 * - Rendition (MEDIA_PREP) → public Supabase Storage thumbnail/preview, used directly.
 *   This is the primary path: the auth-gated /api/media/{id}/image proxy below cannot
 *   work from a plain <img> tag (no Authorization header), so for gdrive:// originals
 *   the rendition is the ONLY thing that renders.
 * - Public http/https file_path → use directly.
 * - Otherwise → backend proxy (only resolves for public files; gdrive:// without a
 *   rendition will show the placeholder).
 */
function resolveImageUrl(media: MediaRecord): string | null {
  const asset = firstAsset(media.media_assets)
  const rendition = asset?.thumbnail_url || asset?.preview_url
  if (rendition) return rendition
  // No server rendition yet — if this is a just-uploaded file, show the user's own
  // copy instantly (object URL) instead of the "Processing…" placeholder.
  const localPreview = getLocalPreview(media.file_name)
  if (localPreview) return localPreview
  const filePath = media.file_path
  if (!filePath) return null
  if (filePath.startsWith('http://') || filePath.startsWith('https://')) return filePath
  const apiBase = import.meta.env.VITE_API_BASE_URL || ''
  return `${apiBase}/api/media/${media.id}/image?size=thumb`
}


// ── Processing banner ─────────────────────────────────────────────────────────
// Explains blank thumbnails / missing labels: when an upload or AI run is still
// processing a deployment the user is looking at, the grid would otherwise show
// empty cards with no reason. Driven by the user's active jobs (GET /api/jobs),
// intersected with the deployments currently in view.
function ProcessingBanner({ deployments }: { deployments: Props['deployments'] }) {
  const { data: jobs } = useJobsList()
  const viewIds = new Set(deployments.map(d => d.id))
  const names = new Map(deployments.map(d => [d.id, d.location_name || d.id.slice(0, 8)]))

  const active = (jobs ?? []).filter(
    j => (j.status === 'queued' || j.status === 'processing') && (j.deployment_ids ?? []).some(id => viewIds.has(id)),
  )
  if (active.length === 0) return null

  const depIds = [...new Set(active.flatMap(j => (j.deployment_ids ?? []).filter(id => viewIds.has(id))))]
  const depNames = depIds.map(id => names.get(id) || id.slice(0, 8))
  const shown = depNames.length <= 3 ? depNames.join(', ') : `${depNames.slice(0, 3).join(', ')} +${depNames.length - 3} more`
  const isOne = depIds.length === 1
  // Progress from the active AI job(s) (now reported per-deployment); the analysing job carries it.
  const pct = Math.round(Math.max(0, ...active.map(j => j.progress ?? 0)) * 100)
  const insightsLink = `/insights?tab=reports${isOne ? `&deployment=${depIds[0]}` : ''}`

  return (
    <div
      role="status"
      style={{
        display: 'flex', flexDirection: 'column', gap: '0.4rem',
        padding: '0.6rem 0.9rem', marginBottom: '0.75rem',
        border: '1px solid rgba(59,130,246,0.4)', borderRadius: 'var(--radius)',
        backgroundColor: 'rgba(59,130,246,0.08)', fontSize: '0.8125rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <span style={{ fontSize: '1rem', animation: 'spin 1.4s linear infinite' }}>⟳</span>
        <span style={{ flex: 1 }}>
          <strong>Analysing {pct}%</strong> — AI is processing {isOne ? 'deployment ' : 'deployments '}
          <strong>{shown}</strong>. Thumbnails and labels appear as they complete.
        </span>
        <Link
          to={insightsLink}
          style={{ whiteSpace: 'nowrap', color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}
          title="See species/detection stats for these images as they're analysed"
        >
          📊 See stats so far →
        </Link>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'rgba(59,130,246,0.2)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--primary, #3b82f6)', borderRadius: 2, transition: 'width 0.4s ease' }} />
      </div>
      <style>{`@keyframes spin { from { transform: rotate(0) } to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}

// ── Component ────────────────────────────────────────────────────────────────

export function MediaBrowser({ deployments, initialDeploymentId, initialSpecies }: Props) {
  const { user } = useAuth()
  // deployment_id → IANA timezone, so capture times render in the photo's local zone.
  const tzByDeployment = useMemo(
    () => new Map(deployments.map(d => [d.id, d.timezone ?? null])),
    [deployments],
  )
  const qc = useQueryClient()
  const { isActive: uploadActive, pendingUploads, pendingSince } = useUploadStore()
  const [reloadKey, setReloadKey] = useState(0)
  // Media whose thumbnail failed to load — shown as "processing" (the rendition
  // is likely still generating). Reset on every (re)load so they re-attempt.
  const [failedThumbs, setFailedThumbs] = useState<Set<string>>(new Set())
  const [media, setMedia]         = useState<MediaRecord[]>([])
  // "Find similar" mode: anchor media id + the resolved, similarity-ranked records.
  const [similarToId, setSimilarToId]     = useState<string | null>(null)
  const [similarRecords, setSimilarRecords] = useState<MediaRecord[]>([])
  const [similarLoading, setSimilarLoading] = useState(false)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [notice, setNotice]       = useState<string | null>(null)
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)
  // The observation a crop card was opened from, pre-selected in the detail.
  const [focusObsId, setFocusObsId] = useState<string | null>(null)

  // ── Primary filters (in ControlBar) ──────────────────────────────────────
  const [filterDeployments, setFilterDeployments] = useState<string[]>([])
  const [filterSpecies, setFilterSpecies]       = useState<string[]>(initialSpecies ? [initialSpecies] : [])
  const [filterStatus, setFilterStatus]         = useState<string>('')
  const [filterAnnotator, setFilterAnnotator]   = useState<string>('')
  const [filterModel, setFilterModel]           = useState<string>('')
  const [filterAnnotationType, setFilterAnnotationType] = useState<string>('')
  const [filterSex, setFilterSex]               = useState<string>('')
  const [filterLifeStage, setFilterLifeStage]   = useState<string>('')
  const [thumbScale, setThumbScale]             = useState(() => {
    const saved = localStorage.getItem('ww:thumbScale')
    return saved ? Number(saved) : 140
  })
  const [groupBy, setGroupBy]                   = useState<GroupBy>(() => {
    return (localStorage.getItem('ww:groupBy') as GroupBy) || 'none'
  })
  // Photo (full frame) vs crop (the detected animal cut-out). Crops make it fast
  // to scan a species for misclassifications; photos reveal animals missing a box.
  const [imageView, setImageView]               = useState<'photo' | 'crop'>(() => {
    return localStorage.getItem('ww:imageView') === 'crop' ? 'crop' : 'photo'
  })
  const [clusterThreshold, setClusterThreshold] = useState(0.0)

  // Persist thumbScale, groupBy and imageView to localStorage
  useEffect(() => { localStorage.setItem('ww:thumbScale', String(thumbScale)) }, [thumbScale])
  useEffect(() => { localStorage.setItem('ww:groupBy', groupBy) }, [groupBy])
  useEffect(() => { localStorage.setItem('ww:imageView', imageView) }, [imageView])

  // ── Advanced filter state ─────────────────────────────────────────────────
  const [advancedOpen, setAdvancedOpen]   = useState(false)
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo]     = useState('')
  const [filterTime, setFilterTime]         = useState<TimeOfDay>('all')

  // WS5-T2: pagination state
  const PAGE_SIZE = 100
  const [page, setPage]           = useState(0)
  const [totalCount, setTotalCount] = useState<number | null>(null)

  // ── Phase 3: iNaturalist publish + tracking ───────────────────────────────
  const inat = useINat()
  const [, setInatMode]         = useState(false)            // iNat selection mode
  const [inatSelected, setInatSelected] = useState<Set<string>>(new Set())
  const [inatStates, setInatStates]     = useState<Map<string, { state: INatState; uri: string | null }>>(new Map())
  const [inatBusy, setInatBusy]         = useState(false)
  const [inatMsg, setInatMsg]           = useState<string | null>(null)

  // ── Phase 4: Unified selection (click = select, double-click = open) ───────
  // Selection is implicit: it's "on" whenever at least one image is selected.
  const [selectedIds, setSelectedIds]     = useState<Set<string>>(new Set())
  // Pending single-click timer, so a fast double-click opens the detail modal
  // without toggling selection first.
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [showDeleteModal, setShowDeleteModal]   = useState(false)
  const [showAiPicker, setShowAiPicker]         = useState(false)
  const [showLabelModal, setShowLabelModal]     = useState(false)
  const [showTrainModal, setShowTrainModal]     = useState(false)
  const [labelBusy, setLabelBusy]               = useState(false)
  const [pipelineLogs, setPipelineLogs]         = useState<string[] | null>(null)
  // Quick in-context connect: paste a personal API token (Pathway 2). The full
  // guided panel lives on the Other page; this is the convenience entry point.
  const connectInat = useCallback(async () => {
    const t = window.prompt(
      'Paste your iNaturalist API token\n(open https://www.inaturalist.org/users/api_token, log in, and copy the api_token value):',
    )
    if (!t || !t.trim()) return
    try {
      await inat.setToken(t)
    } catch {
      setInatMsg('iNaturalist token was rejected — copy the full api_token and try again.')
    }
  }, [inat])

  // const toggleInat = (id: string) => setInatSelected(prev => {
  //   const next = new Set(prev)
  //   if (next.has(id)) next.delete(id)
  //   else next.add(id)
  //   return next
  // })

  // Load iNat upload/sync state for a set of media (graceful if table absent).
  const loadInatStates = useCallback(async (ids: string[]) => {
    if (ids.length === 0) { setInatStates(new Map()); return }
    const { data, error: err } = await supabase
      .from('inat_observation_media')
      .select('media_id, inat_observations(sync_status, inat_uri)')
      .in('media_id', ids)
    if (err || !data) return  // table not migrated yet / no access → no badges
    const map = new Map<string, { state: INatState; uri: string | null }>()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    for (const row of data as any[]) {
      const io = Array.isArray(row.inat_observations) ? row.inat_observations[0] : row.inat_observations
      if (io?.sync_status) map.set(row.media_id, { state: io.sync_status as INatState, uri: io.inat_uri ?? null })
    }
    setInatStates(map)
  }, [])

  // ── Unified selection helpers ──────────────────────────────────────────────
  const toggleSelect = useCallback((id: string) => setSelectedIds(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  }), [])

  // Single click toggles selection (after a short delay); a second click within
  // the window cancels the toggle and opens the full-screen detail instead.
  const handleCardClick = useCallback((id: string) => {
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
      setSelectedMediaId(id)
    } else {
      clickTimer.current = setTimeout(() => {
        clickTimer.current = null
        toggleSelect(id)
      }, 250)
    }
  }, [toggleSelect])

  useEffect(() => () => { if (clickTimer.current) clearTimeout(clickTimer.current) }, [])

  const handleBulkAction = useCallback(async (action: BulkAction) => {
    if (action === 'similar') {
      // Anchor on the single selected image and enter similarity-ranked mode.
      const anchor = [...selectedIds][0]
      if (anchor) { setSimilarToId(anchor); setSelectedIds(new Set()) }
    } else if (action === 'delete') {
      setShowDeleteModal(true)
    } else if (action === 'ai') {
      setShowAiPicker(true)
    } else if (action === 'label') {
      setShowLabelModal(true)
    } else if (action === 'train') {
      setShowTrainModal(true)
    } else if (action === 'inat') {
      if (!inat.connected) {
        connectInat()
        return
      }
      // Upload the unified selection. Pass the ids directly: setInatSelected is
      // async, so uploadToInat() reading inatSelected here would see the stale
      // (empty) set and silently no-op.
      setInatSelected(selectedIds)
      setInatMode(true)
      uploadToInat(selectedIds)
    }
    // syncFromInat/uploadToInat are plain (unmemoised) functions declared below;
    // including them would just defeat the memo without changing behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inat.connected, connectInat, selectedIds])

  const handleBatchDelete = useCallback(async (ids: string[]) => {
    // Capture the rows so Undo can re-insert them without a full refetch.
    const removed = media.filter(m => ids.includes(m.id))
    const res = await apiClient.del('/api/media/batch', { media_ids: ids }) as { data?: { deleted_at?: string } }
    const deletedAt = res?.data?.deleted_at
    setMedia(prev => prev.filter(m => !ids.includes(m.id)))
    setSelectedIds(new Set())
    if (deletedAt) {
      showUndoToast({
        message: `Deleted ${ids.length} photo${ids.length !== 1 ? 's' : ''}`,
        onUndo: async () => {
          await apiClient.post('/api/media/batch/restore', { media_ids: ids, deleted_at: deletedAt })
          setMedia(prev => {
            const have = new Set(prev.map(m => m.id))
            return [...removed.filter(m => !have.has(m.id)), ...prev]
          })
        },
      })
    }
  }, [media])

  const handleRunAi = useCallback(async (models: string[]) => {
    const ids = Array.from(selectedIds)
    setPipelineLogs([`Starting AI pipeline for ${ids.length} images with models: ${models.join(', ')}…`])
    try {
      const resp = await fetch('/api/media/run-selected', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ media_ids: ids, steps: models }),
      })
      if (!resp.ok) throw new Error('Pipeline request failed')
      const data = await resp.json()
      setPipelineLogs(prev => [...(prev || []), `✓ Job queued: ${data.data?.job_id ?? 'unknown'}`])
    } catch (e: any) {
      setPipelineLogs(prev => [...(prev || []), `⚠ Error: ${e?.message || 'unknown'}`])
    }
  }, [selectedIds])

  // Honour ?deployment=<id>[,<id>...] from the upload redirect and the dock link. One id focuses
  // that deployment; several (an upload that spanned deployments) select them all, and the
  // deployment pill reads "N deployments".
  useEffect(() => {
    if (!initialDeploymentId) return
    const ids = initialDeploymentId.split(',').map(s => s.trim()).filter(Boolean)
    if (ids.length) { setFilterDeployments(ids); setPage(0) }
  }, [initialDeploymentId])



  // Reset page when deployment filter changes
  useEffect(() => { setPage(0) }, [filterDeployments, deployments])

  // ── Fetch media (with pagination) ─────────────────────────────────────────
  useEffect(() => {
    if (!user) return

    const deploymentIds = filterDeployments.length
      ? filterDeployments
      : deployments.map(d => d.id)

    if (deploymentIds.length === 0) {
      setMedia([])
      setTotalCount(0)
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)

    const from = page * PAGE_SIZE
    const to   = from + PAGE_SIZE - 1

    supabase
      .from('media')
      .select(MEDIA_SELECT, { count: 'exact' })
      .in('deployment_id', deploymentIds)
      .is('deleted_at', null)
      .order('timestamp', { ascending: false, nullsFirst: false })
      .range(from, to)
      .then(({ data, error: err, count }) => {
        if (cancelled) return
        if (err) { setError(err.message); setLoading(false); return }
        setMedia((data || []) as unknown as MediaRecord[])
        setTotalCount(count ?? null)
        setFailedThumbs(new Set())  // re-attempt thumbnails (renditions may now exist)
        setLoading(false)
      })

    return () => { cancelled = true }
  }, [user, deployments, filterDeployments, page, reloadKey])

  // While an upload is running, poll so newly-registered media appear in the grid
  // without a manual refresh, and do one final refresh when it finishes (cleanup
  // runs on the isActive → false transition, catching the last batch).
  useEffect(() => {
    if (!uploadActive) return
    const t = setInterval(() => setReloadKey(k => k + 1), 4000)
    return () => { clearInterval(t); setReloadKey(k => k + 1) }
  }, [uploadActive])

  // Refresh iNaturalist badges whenever the loaded media set changes.
  useEffect(() => { loadInatStates(media.map(m => m.id)) }, [media, loadInatStates])

  // Upload the iNat-selected media to iNaturalist (burst-consolidated).
  // Accepts an explicit id set so callers can avoid the async-state trap;
  // falls back to inatSelected when called without args.
  const uploadToInat = async (ids?: Set<string>) => {
    const toPublish = ids ?? inatSelected
    if (toPublish.size === 0 || inatBusy) return
    setInatBusy(true)
    setInatMsg('⬆ Uploading to iNaturalist…')
    try {
      const r = await inat.publish([...toPublish])
      setInatMsg(
        `✓ ${r.observations_created} observation(s), ${r.photos_uploaded} photo(s)` +
        (r.skipped_bycatch ? ` · ${r.skipped_bycatch} by-catch skipped` : '') +
        (r.skipped_already_published ? ` · ${r.skipped_already_published} already on iNat` : '') +
        (r.errors ? ` · ${r.errors} error(s)` : ''),
      )
      setInatSelected(new Set())
      await loadInatStates(media.map(m => m.id))
    } catch (e) {
      setInatMsg(`⚠ ${(e as Error)?.message ?? 'Upload failed'}`)
    } finally {
      setInatBusy(false)
    }
  }

  // Pull community identifications from iNaturalist and refresh the badges.
  // Bulk-label: write one human observation per selected image (animal species,
  // or a blank when selection is null). iNat community-ID sync now lives in
  // Settings (and auto-runs on login), not here.
  const bulkLabel = async (selection: SpeciesSelection | null) => {
    if (labelBusy) return
    const ids = [...selectedIds]
    if (ids.length === 0) { setShowLabelModal(false); return }
    setLabelBusy(true)
    const depByMedia = new Map(media.map(m => [m.id, m.deployment_id]))
    const rows = ids
      .filter(mid => depByMedia.get(mid))
      .map(mid => ({
        deployment_id: depByMedia.get(mid)!,
        media_id: mid,
        observation_level: 'media',
        observation_type: selection ? 'animal' : 'blank',
        taxon_id: selection?.taxon_id ?? null,
        scientific_name: selection?.scientific_name ?? null,
        vernacular_name: selection?.vernacular_name ?? null,
        ...humanCreateFields({ userId: user?.id, userEmail: user?.email }),
      }))
    if (rows.length === 0) { setLabelBusy(false); setShowLabelModal(false); return }
    const { error } = await supabase.from('observations').insert(rows)
    setLabelBusy(false)
    if (error) {
      setNotice(`Error: ${error.message}`)
    } else {
      setShowLabelModal(false)
      setSelectedIds(new Set())
      setReloadKey(k => k + 1)
      setNotice(`Labelled ${rows.length} image${rows.length !== 1 ? 's' : ''} as ${selection ? selection.scientific_name : 'blank'}`)
    }
    window.setTimeout(() => setNotice(null), 4000)
  }

  // ── Derived filter options ────────────────────────────────────────────────
  const speciesList = useMemo(() => {
    const names = new Set<string>()
    media.forEach(m => m.observations.forEach(o => { if (o.scientific_name) names.add(o.scientific_name) }))
    return Array.from(names).sort()
  }, [media])

  const humanAnnotatorList = useMemo(() => {
    const names = new Set<string>()
    media.forEach(m => m.observations.forEach(o => {
      if (o.classification_method === 'human' && o.classified_by) names.add(o.classified_by)
    }))
    return Array.from(names).sort()
  }, [media])

  const modelList = useMemo(() => {
    const models = new Set<string>()
    media.forEach(m => m.observations.forEach(o => {
      const v = o.source_model_version || (o.classification_method === 'machine' ? o.classified_by : null)
      if (v) models.add(v)
    }))
    return Array.from(models).sort().map(v => ({
      value: v,
      label: v.startsWith('speciesnet') ? `SpeciesNet (${v.split('-').pop()})` :
             v.startsWith('bioclip')    ? `BioCLIP (${v.split('-').pop()})` : v,
    }))
  }, [media])

  const sexList = useMemo(() => {
    const vals = new Set<string>()
    media.forEach(m => m.observations.forEach(o => { if (o.sex) vals.add(o.sex) }))
    return Array.from(vals).sort()
  }, [media])

  const lifeStageList = useMemo(() => {
    const vals = new Set<string>()
    media.forEach(m => m.observations.forEach(o => { if (o.life_stage) vals.add(o.life_stage) }))
    return Array.from(vals).sort()
  }, [media])

  // ── Client-side filter chain ──────────────────────────────────────────────
  // Optimistic cards for files still uploading (no DB row yet), shown instantly via the local
  // preview so the grid isn't empty right after an upload redirect. Scoped to the deployment(s)
  // in view and retired one for one as real rows land (see lib/pendingCards for why a name match
  // is not enough).
  const pendingCards = useMemo<MediaRecord[]>(() => {
    if (pendingUploads.length === 0) return []
    const viewIds = new Set(filterDeployments.length ? filterDeployments : deployments.map(d => d.id))
    return survivingPending(pendingUploads, media, pendingSince, viewIds).map(p => ({
      id: `pending:${p.fileName.toLowerCase()}`, deployment_id: p.deploymentId, file_path: '', file_name: p.fileName,
      file_mediatype: 'image/jpeg', timestamp: null, file_public: false, media_comments: null,
      exif_metadata: null, media_assets: null, observations: [], _pending: true,
    }))
  }, [pendingUploads, pendingSince, media, filterDeployments, deployments])

  const filtered = useMemo(() => {
    // Real (viewable) media first; still-uploading placeholders appended last so the user
    // interacts with available photos rather than "Uploading" cards.
    let result: MediaRecord[] = pendingCards.length ? [...media, ...pendingCards] : media

    if (filterSpecies.length) {
      result = result.filter(m => m.observations.some(o => !!o.scientific_name && filterSpecies.includes(o.scientific_name)))
    }
    if (filterAnnotator) {
      result = result.filter(m => m.observations.some(o =>
        o.classification_method === 'human' && o.classified_by === filterAnnotator
      ))
    }
    if (filterModel) {
      result = result.filter(m => m.observations.some(o =>
        (o.source_model_version === filterModel) ||
        (o.classification_method === 'machine' && o.classified_by === filterModel)
      ))
    }
    if (filterAnnotationType) {
      result = result.filter(m => {
        const hasBbox = m.observations.some(o => o.bbox_x != null)
        return filterAnnotationType === 'bbox' ? hasBbox : !hasBbox
      })
    }
    if (filterSex) {
      result = result.filter(m => m.observations.some(o => o.sex === filterSex))
    }
    if (filterLifeStage) {
      result = result.filter(m => m.observations.some(o => o.life_stage === filterLifeStage))
    }
    if (filterStatus) {
      result = result.filter(m => {
        const status = deriveAnnotationStatus({
          hasReviewed: m.observations.some(isHumanReviewed),
          hasAi:       m.observations.some(isAiLabel),
        })
        return status === (filterStatus as AnnotationStatus)
      })
    }
    if (filterDateFrom) {
      result = result.filter(m => !m.timestamp || m.timestamp >= filterDateFrom)
    }
    if (filterDateTo) {
      result = result.filter(m => !m.timestamp || m.timestamp <= filterDateTo + 'T23:59:59')
    }
    if (filterTime !== 'all') {
      result = result.filter(m => getTimeOfDay(m.timestamp, tzByDeployment.get(m.deployment_id)) === filterTime)
    }

    return result
  }, [media, pendingCards, filterSpecies, filterAnnotator, filterModel, filterAnnotationType, filterSex, filterLifeStage, filterStatus, filterDateFrom, filterDateTo, filterTime, tzByDeployment])

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ctrl/Cmd+A: select all visible media (once a selection has started)
      if ((e.ctrlKey || e.metaKey) && e.key === 'a' && selectedIds.size > 0) {
        e.preventDefault()
        setSelectedIds(new Set(filtered.map(m => m.id)))
      }
      // Escape: clear selection or close detail modal
      if (e.key === 'Escape') {
        if (selectedIds.size > 0) {
          setSelectedIds(new Set())
        } else if (selectedMediaId) {
          setSelectedMediaId(null)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [selectedIds.size, filtered, selectedMediaId])

  // ── Cluster data (only fetched when groupBy === 'cluster') ─────────────────
  const activeDeploymentIds = useMemo(() => {
    if (filterDeployments.length) return filterDeployments
    return deployments.map(d => d.id)
  }, [deployments, filterDeployments])
  const clustersQ = useMultiClusters(
    groupBy === 'cluster' ? activeDeploymentIds : [],
    clusterThreshold,
  )

  // Map a cluster group's display label → its confirm-able row id, so the group
  // header can offer "label all" (only for unconfirmed clusters). Mirrors the
  // label logic in groupedMedia below.
  const clusterByLabel = useMemo(() => {
    const m = new Map<string, { id: string; confirmed: boolean }>()
    if (groupBy !== 'cluster') return m
    for (const c of clustersQ.data?.clusters ?? []) {
      const label = c.scientific_name ?? `Cluster ${c.cluster_id}`
      if (!m.has(label)) m.set(label, { id: c.id, confirmed: !!c.scientific_name })
    }
    return m
  }, [groupBy, clustersQ.data])

  // After a cluster is bulk-confirmed: refresh the cluster assignments (relabels
  // the group) and reload media so the new observations show on the thumbnails.
  const onClusterConfirmed = useCallback((created: number, name: string) => {
    qc.invalidateQueries({ queryKey: ['brain', 'clusters-multi'] })
    setReloadKey(k => k + 1)
    setNotice(`Labelled cluster as ${name}${created ? ` · ${created} observation${created !== 1 ? 's' : ''}` : ''}`)
    window.setTimeout(() => setNotice(null), 4000)
  }, [qc])

  // ── Find-similar data ──────────────────────────────────────────────────────
  // Embedding nearest-neighbours for the anchor image; results are fetched as
  // full media records (so they label/select like any grid card) and kept in
  // similarity order.
  const similarQ = useSimilarImages(similarToId, 30)
  useEffect(() => {
    if (!similarToId) { setSimilarRecords([]); return }
    const hits = similarQ.data?.results
    if (!hits) return
    let cancelled = false
    setSimilarLoading(true)
    const ids = [similarToId, ...hits.map(h => h.media_id)]
    const rank = new Map(ids.map((id, i) => [id, i]))
    supabase
      .from('media')
      .select(MEDIA_SELECT)
      .in('id', ids)
      .is('deleted_at', null)
      .then(({ data }) => {
        if (cancelled) return
        const recs = ((data || []) as unknown as MediaRecord[])
          .sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0))
        setSimilarRecords(recs)
        setSimilarLoading(false)
      })
    return () => { cancelled = true }
  }, [similarToId, similarQ.data])

  // ── Grouped media computation ─────────────────────────────────────────────
  const groupedMedia = useMemo(() => {
    if (groupBy === 'none') return null

    const groups = new Map<string, MediaRecord[]>()
    const deploymentNames = new Map(deployments.map(d => [d.id, d.location_name || d.id.slice(0, 8)]))

    // Cluster lookups (from the multi-cluster API): media → cluster_id, the set
    // of outliers, and any confirmed species name per cluster for nicer labels.
    const clusterData = clustersQ.data
    const mediaCluster = clusterData?.media_clusters ?? {}
    const outlierSet = new Set(clusterData?.outlier_media_ids ?? [])
    const clusterNames = new Map<number, string>()
    for (const c of clusterData?.clusters ?? []) {
      if (c.scientific_name && !clusterNames.has(c.cluster_id)) clusterNames.set(c.cluster_id, c.scientific_name)
    }

    for (const m of filtered) {
      let key: string
      switch (groupBy) {
        case 'cluster': {
          if (!clusterData) { key = '(Loading clusters…)'; break }
          if (outlierSet.has(m.id)) { key = '🟡 Outliers'; break }
          const cid = mediaCluster[m.id]
          // Not in any cluster yet (e.g. blank frame with no animal crop, or
          // embeddings not run for this deployment).
          if (cid == null) { key = '⧗ Not yet clustered'; break }
          key = clusterNames.get(cid) ?? `Cluster ${cid}`
          break
        }
        case 'species':
          key = m.observations[0]?.scientific_name || '(No species)'
          break
        case 'sex':
          key = m.observations[0]?.sex || '(Unknown)'
          break
        case 'life_stage':
          key = m.observations[0]?.life_stage || '(Unknown)'
          break
        case 'annotation_type':
          key = m.observations.some(o => o.bbox_x != null) ? 'Bounding box' : 'Whole image'
          break
        case 'deployment':
          key = deploymentNames.get(m.deployment_id) || m.deployment_id.slice(0, 8)
          break
        case 'model': {
          const aiObs = m.observations.find(o => o.classification_method === 'machine')
          key = aiObs?.source_model_version || aiObs?.classified_by || '(No AI model)'
          break
        }
        case 'annotator': {
          const humanObs = m.observations.find(o => o.classification_method === 'human')
          key = humanObs?.classified_by || '(No annotator)'
          break
        }
        default:
          key = '(Ungrouped)'
      }
      const list = groups.get(key) || []
      list.push(m)
      groups.set(key, list)
    }

    // Sort groups by size descending
    return Array.from(groups.entries()).sort((a, b) => b[1].length - a[1].length)
  }, [filtered, groupBy, deployments, clustersQ.data])

  // ── KPI stats ─────────────────────────────────────────────────────────────
  // Over real rows only: an optimistic card has no file_path by construction, and counting it
  // as "no image" (and as media) made the header read "10 no image" during every upload.
  const stats = useMemo(() => {
    const real = filtered.filter(m => !m._pending)
    return {
      total:          real.length,
      uploading:      filtered.length - real.length,
      withDetections: real.filter(m => m.observations.some(isAiLabel)).length,
      annotated:      real.filter(m => m.observations.some(isHumanReviewed)).length,
      noImage:        real.filter(m => !m.file_path).length,
    }
  }, [filtered])

  // Number of cards the Labels view renders this page (AI crops + human labels).
  const cropCardCount = useMemo(() => {
    if (imageView !== 'crop') return 0
    let n = 0
    for (const m of filtered) n += m.observations.filter(isLabelCard).length
    return n
  }, [filtered, imageView])

  const hasAdvancedFilters = !!(
    filterStatus || filterAnnotator || filterModel || filterAnnotationType || filterSex || filterLifeStage ||
    filterDateFrom || filterDateTo || filterTime !== 'all'
  )

  const clearAdvanced = () => {
    setFilterStatus(''); setFilterAnnotator(''); setFilterModel(''); setFilterAnnotationType(''); setFilterSex(''); setFilterLifeStage('')
    setFilterDateFrom('')
    setFilterDateTo('')
    setFilterTime('all')
  }

  const selectedMedia = filtered.find(m => m.id === selectedMediaId) || null

  const handleMediaUpdated = (updated: MediaRecord) => {
    setMedia(prev => prev.map(m => m.id === updated.id ? updated : m))
  }

  // AN-4: after a one-click Confirm/Blank, jump to the next image in the grid.
  const advanceToNext = () => {
    if (!selectedMediaId) return
    const idx = filtered.findIndex(m => m.id === selectedMediaId)
    const next = idx >= 0 && idx < filtered.length - 1 ? filtered[idx + 1] : null
    setSelectedMediaId(next ? next.id : null)
  }

  // Step back to the previous image in the grid (modal ‹ arrow).
  const advanceToPrev = () => {
    if (!selectedMediaId) return
    const idx = filtered.findIndex(m => m.id === selectedMediaId)
    if (idx > 0) setSelectedMediaId(filtered[idx - 1].id)
  }

  if (deployments.length === 0) {
    return <p style={{ opacity: 0.6, padding: '2rem 0' }}>Select a project or add deployments to browse media.</p>
  }

  // Pagination button style helper
  const PAGE_BTN = (disabled: boolean): React.CSSProperties => ({
    padding: '0.3rem 0.6rem', fontSize: '0.8125rem',
    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
    backgroundColor: 'transparent', cursor: disabled ? 'not-allowed' : 'pointer',
    color: disabled ? 'var(--text-color)' : 'var(--primary)',
    opacity: disabled ? 0.35 : 1,
  })

  const minWidth = thumbScale
  const height = Math.round(thumbScale * 0.78)

  // ── Thumbnail card renderer (shared by flat + grouped grids) ──────────────
  const renderThumbCard = (m: MediaRecord) => {
    const imgUrl = resolveImageUrl(m)

    // Optimistic (still-uploading) card — display-only, no selection/detail/actions.
    if (m._pending) {
      return (
        <div key={m.id} title="Uploading…" style={{
          border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden',
          backgroundColor: 'var(--surface)', opacity: 0.9,
        }}>
          <div style={{ position: 'relative', aspectRatio: '4 / 3', backgroundColor: 'var(--surface)' }}>
            {imgUrl ? (
              <img src={imgUrl} alt={m.file_name ?? ''} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', fontSize: '0.7rem', opacity: 0.5 }}>Uploading…</div>
            )}
            <span style={{ position: 'absolute', top: 6, left: 6, background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '0.62rem', padding: '2px 6px', borderRadius: 4 }}>⬆ Uploading</span>
          </div>
          <div style={{ padding: '0.4rem 0.5rem', fontSize: '0.68rem', opacity: 0.65, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.file_name}</div>
        </div>
      )
    }

    // WS5-T4 / AN-2: derive status badge from the review_status contract
    const annotStatus = deriveAnnotationStatus({
      hasReviewed: m.observations.some(isHumanReviewed),
      hasAi:       m.observations.some(isAiLabel),
    })

    // Top label: human-reviewed first, then AI
    const sortedObs = [...m.observations].sort((a, b) => {
      const ar = isHumanReviewed(a) ? 1 : 0
      const br = isHumanReviewed(b) ? 1 : 0
      return br - ar
    })
    const topObs = sortedObs[0] || null
    const isEmpty = !!topObs && !topObs.scientific_name && topObs.observation_type === 'blank'
    const label  = topObs?.scientific_name || (isEmpty ? 'Empty' : null)
    const conf   = topObs?.classification_probability ?? null
    const aiConfs = m.observations
      .filter(o => isAiLabel(o) && o.classification_probability != null)
      .map(o => o.classification_probability as number)
    const lowestConf = aiConfs.length ? Math.min(...aiConfs) : null
    const isSelected = selectedMediaId === m.id
    const inatSt  = inatStates.get(m.id)
    const sel = selectedIds.has(m.id)

    return (
      <div
        key={m.id}
        onClick={() => { setFocusObsId(null); handleCardClick(m.id) }}
        title="Click to select · double-click to open"
        style={{
          border: sel ? '2px solid var(--primary)' : isSelected ? '2px solid var(--primary)' : '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          overflow: 'hidden',
          cursor: 'pointer',
          backgroundColor: 'var(--surface)',
          transition: 'border-color 0.15s, transform 0.15s',
          transform: isSelected ? 'scale(1.02)' : undefined,
          userSelect: 'none',
        }}
      >
        {/* Thumbnail area */}
        <div style={{
          height,
          backgroundColor: imgUrl ? undefined : 'rgba(0,0,0,0.04)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
          position: 'relative',
        }}>
          {imgUrl && !failedThumbs.has(m.id) ? (
            <img
              src={imgUrl}
              alt={m.file_name || 'media'}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={() => setFailedThumbs(s => new Set(s).add(m.id))}
            />
          ) : imgUrl ? (
            // Thumbnail not ready yet (rendition still generating / resolving).
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem', opacity: 0.5, fontSize: '0.72rem' }}>
              <span style={{ fontSize: '1.4rem' }}>⏳</span>
              <span>Processing…</span>
            </div>
          ) : (
            <span style={{ fontSize: '2rem', opacity: 0.3 }}>📷</span>
          )}

          {/* WS5-T4: Status badge — top-left overlay */}
          <span style={{ position: 'absolute', top: 4, left: 4 }}>
            <StatusBadge
              status={annotStatus}
              size="sm"
              label={
                annotStatus === 'ai' && lowestConf !== null
                  ? `${(lowestConf * 100).toFixed(0)}%`
                  : undefined
              }
            />
          </span>

          {/* Phase 3: iNaturalist dove badge — top-right overlay */}
          {inatSt && (
            <span style={{ position: 'absolute', top: 4, right: 4 }}>
              <INatBadge state={inatSt.state} uri={inatSt.uri} />
            </span>
          )}

          {/* Selection checkmark — bottom-left, shown while a selection is active */}
          {(sel || selectedIds.size > 0) && (
            <span style={{
              position: 'absolute', bottom: 4, left: 4, width: 18, height: 18,
              borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '0.7rem', fontWeight: 700,
              backgroundColor: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)',
              color: '#fff', boxShadow: '0 0 0 1.5px rgba(255,255,255,0.85)',
            }}>
              {sel ? '✓' : ''}
            </span>
          )}
        </div>

        {/* Label bar */}
        <div style={{ padding: '0.375rem 0.5rem', fontSize: '0.6875rem' }}>
          <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {topObs?.scientific_name
              ? label
              : isEmpty
                ? <span style={{ opacity: 0.6, fontStyle: 'italic' }}>Empty</span>
                : <span style={{ opacity: 0.4 }}>No label</span>}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', opacity: 0.6, fontSize: '0.625rem' }}>
            <span>{m.file_name || m.file_path.split('/').pop()}</span>
            {conf !== null && <span>{(conf * 100).toFixed(0)}%</span>}
          </div>
          {m.timestamp && (
            <div style={{ opacity: 0.5, fontSize: '0.625rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {formatCaptureTime(m.timestamp, tzByDeployment.get(m.deployment_id))}
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Crop view: one card per observation ────────────────────────────────────
  // Renders an individual detection's crop (observations.crop_url). Selection and
  // open are keyed by media id (actions are media-level), so the existing grid
  // machinery is reused. Falls back to the full frame when a crop is missing.
  const renderCropCard = (m: MediaRecord, obs: ObservationRecord | null, key: string) => {
    const cropUrl = obs?.crop_url || null
    const imgUrl = cropUrl || resolveImageUrl(m)
    const noCrop = !cropUrl
    const sel = selectedIds.has(m.id)
    const isSelected = selectedMediaId === m.id
    const status = deriveAnnotationStatus({
      hasReviewed: !!obs && isHumanReviewed(obs),
      hasAi: !!obs && isAiLabel(obs),
    })
    const conf = obs?.classification_probability ?? null
    const isEmpty = !!obs && !obs.scientific_name && obs.observation_type === 'blank'
    const label = obs?.scientific_name || null

    return (
      <div
        key={key}
        onClick={() => { setFocusObsId(obs?.id ?? null); handleCardClick(m.id) }}
        title="Click to select · double-click to open"
        style={{
          border: sel || isSelected ? '2px solid var(--primary)' : '1px solid var(--border)',
          borderRadius: 'var(--radius)', overflow: 'hidden', cursor: 'pointer',
          backgroundColor: 'var(--surface)', transition: 'border-color 0.15s, transform 0.15s',
          transform: isSelected ? 'scale(1.02)' : undefined, userSelect: 'none',
        }}
      >
        <div style={{ height, backgroundColor: imgUrl ? undefined : 'rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', position: 'relative' }}>
          {imgUrl ? (
            <img
              src={imgUrl}
              alt={label || 'crop'}
              // Crops are tight cut-outs — 'contain' shows the whole animal;
              // a full-frame fallback fills the card with 'cover'.
              style={{ width: '100%', height: '100%', objectFit: noCrop ? 'cover' : 'contain' }}
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <span style={{ fontSize: '2rem', opacity: 0.3 }}>📷</span>
          )}
          <span style={{ position: 'absolute', top: 4, left: 4 }}>
            <StatusBadge status={status} size="sm" label={status === 'ai' && conf !== null ? `${(conf * 100).toFixed(0)}%` : undefined} />
          </span>
          {noCrop && imgUrl && (
            <span style={{ position: 'absolute', bottom: 4, right: 4, fontSize: '0.6rem', padding: '0.05rem 0.3rem', borderRadius: '3px', background: 'rgba(0,0,0,0.55)', color: '#fff' }}>
              full frame
            </span>
          )}
          {(sel || selectedIds.size > 0) && (
            <span style={{ position: 'absolute', bottom: 4, left: 4, width: 18, height: 18, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: 700, backgroundColor: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)', color: '#fff', boxShadow: '0 0 0 1.5px rgba(255,255,255,0.85)' }}>
              {sel ? '✓' : ''}
            </span>
          )}
        </div>
        <div style={{ padding: '0.375rem 0.5rem', fontSize: '0.6875rem' }}>
          <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {label || (isEmpty ? <span style={{ opacity: 0.6, fontStyle: 'italic' }}>Empty</span> : <span style={{ opacity: 0.4 }}>No label</span>)}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', opacity: 0.6, fontSize: '0.625rem' }}>
            <span>{m.file_name || m.file_path.split('/').pop()}</span>
            {conf !== null && <span>{(conf * 100).toFixed(0)}%</span>}
          </div>
        </div>
      </div>
    )
  }

  // Expand a media into one card per label: AI crops + human labels (full-frame
  // when they have no crop). Un-labelled / cropless AI frames are omitted.
  const cropCardsFor = (m: MediaRecord) =>
    m.observations.filter(isLabelCard).map(o => renderCropCard(m, o, o.id))

  return (
    <div>
      {/* ── "Being processed" banner for in-view deployments ───────── */}
      <ProcessingBanner deployments={deployments} />

      {/* ── Ribbon command bar (sticky so filters/actions stay reachable
          while scrolling the photo grid) ──────────────────────────── */}
      <Ribbon
        sticky
        status={
          <div style={{ display: 'flex', alignItems: 'center' }}>
            {/* ── Stats (read-only; first to drop when the row is tight) ── */}
            <span className="ribbon-stats" style={{ opacity: 0.6, whiteSpace: 'nowrap' }} title="Results in the current view">
              <strong>{totalCount ?? stats.total}</strong> media{totalCount !== null && totalCount > PAGE_SIZE && <span style={{ opacity: 0.8 }}> ({stats.total} shown)</span>}
              {' · '}<strong>{stats.withDetections}</strong> detections
              {' · '}<strong>{stats.annotated}</strong> annotated
              {imageView === 'crop' && <> · <strong>{cropCardCount}</strong> labels</>}
              {stats.noImage > 0 && <span style={{ color: 'var(--warning, #f59e0b)' }}> · {stats.noImage} no image</span>}
              {stats.uploading > 0 && <span style={{ color: 'var(--primary)' }}> · {stats.uploading} uploading</span>}
            </span>

            {/* ── Display controls (what to show) ── */}
            <span style={RIBBON_SECTION}>
              <span style={{ display: 'flex', flexShrink: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }} title="Photo shows the full frame; Labels shows each cropped, labelled detection">
                {(['photo', 'crop'] as const).map(v => (
                  <button key={v} onClick={() => setImageView(v)}
                    style={{ fontSize: '0.72rem', padding: '0.2rem 0.55rem', border: 'none', cursor: 'pointer',
                      background: imageView === v ? 'var(--primary)' : 'transparent', color: imageView === v ? '#fff' : 'var(--text-color)', fontWeight: imageView === v ? 600 : 400 }}>
                    {v === 'photo' ? '🖼 Photo' : '🏷️ Labels'}
                  </button>
                ))}
              </span>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', opacity: 0.7, flexShrink: 0 }} title="Thumbnail size">
                <span>🔍</span>
                <input type="range" min={80} max={280} step={10} value={thumbScale} onChange={e => setThumbScale(+e.target.value)}
                  style={{ width: 90, accentColor: 'var(--primary)', cursor: 'pointer' }} />
              </label>
            </span>

            {/* ── Selection / actions ── */}
            <span style={RIBBON_SECTION}>
              <MediaBulkActions
                selectedCount={selectedIds.size}
                onSelectAll={() => setSelectedIds(new Set(filtered.map(m => m.id)))}
                onClearSelection={() => setSelectedIds(new Set())}
                onAction={handleBulkAction}
                inatConnected={!!inat.connected}
              />
            </span>
          </div>
        }
        subBar={(groupBy !== 'none' || hasAdvancedFilters) ? (
          <>
            {/* Active grouping is persisted — surface it so it's never a surprise, and one-click clearable. */}
            {groupBy !== 'none' && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.78rem', padding: '0.2rem 0.55rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-color)' }}>
                ▦ Grouped by <strong>{GROUP_LABELS[groupBy]}</strong>
                <button onClick={() => setGroupBy('none')} title="Clear grouping" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.85rem', lineHeight: 1, padding: 0 }}>✕</button>
              </span>
            )}
            {hasAdvancedFilters && (
              <button onClick={clearAdvanced}
                style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem', color: 'var(--primary)', padding: '0.2rem 0.5rem' }}>
                ✕ Clear advanced filters
              </button>
            )}
          </>
        ) : undefined}
        tabs={[
          {
            id: 'filter', label: 'Filter', icon: '⛃',
            groups: [
              { id: 'deployment', title: 'Deployment', content: (
                <MultiSelect values={filterDeployments} onChange={setFilterDeployments} allLabel="All deployments" noun="deployment"
                  options={deployments.map(d => ({ value: d.id, label: d.location_name || d.id.slice(0, 8) }))} />
              ) },
              { id: 'species', title: 'Species', content: (
                <MultiSelect values={filterSpecies} onChange={setFilterSpecies} allLabel="All species" noun="species"
                  options={speciesList.map(s => ({ value: s, label: s }))} />
              ) },
              {
                id: 'refine', title: 'More filters',
                launcher: () => setAdvancedOpen(true),
                launcherTitle: 'Advanced filters (status, annotator, model, sex, life stage, date, day/night)',
                launcherActive: hasAdvancedFilters,
                content: (
                  <button
                    onClick={() => setAdvancedOpen(true)}
                    style={{
                      padding: '0.35rem 0.7rem', fontSize: '0.75rem', cursor: 'pointer',
                      borderRadius: 'var(--radius)', whiteSpace: 'nowrap',
                      border: `1px solid ${hasAdvancedFilters ? 'var(--primary)' : 'var(--border)'}`,
                      backgroundColor: hasAdvancedFilters ? 'rgba(76,175,80,0.1)' : 'transparent',
                      color: hasAdvancedFilters ? 'var(--primary)' : 'var(--text-color)',
                    }}
                  >
                    ⚙ Advanced filters{hasAdvancedFilters ? ' ●' : ''}
                  </button>
                ),
              },
            ],
          },
          {
            id: 'group', label: 'Group', icon: '▦',
            groups: [
              { id: 'group-mode', title: 'Group by', content: (
                <FilterSelect value={groupBy} onChange={v => setGroupBy(v as GroupBy)} placeholder="None"
                  options={[
                    { value: 'none',            label: '— None' },
                    { value: 'cluster',         label: '🧠 Cluster (embeddings)' },
                    { value: 'species',         label: '🐾 Species' },
                    { value: 'sex',             label: '♀♂ Sex' },
                    { value: 'life_stage',      label: '🌱 Life stage' },
                    { value: 'annotation_type', label: '▣ Annotation type' },
                    { value: 'deployment',      label: '📍 Deployment' },
                    { value: 'model',           label: '🤖 AI model' },
                    { value: 'annotator',       label: '👤 Annotator' },
                  ]} />
              ) },
              ...(groupBy === 'cluster' ? [{ id: 'cluster-threshold', title: 'Similarity', content: (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <span style={{ fontSize: '0.7rem', opacity: 0.6 }}>Loose</span>
                  <input type="range" min={0} max={1} step={0.05} value={clusterThreshold}
                    onChange={e => setClusterThreshold(+e.target.value)}
                    style={{ width: 80, accentColor: 'var(--primary)', cursor: 'pointer' }} />
                  <span style={{ fontSize: '0.7rem', opacity: 0.6 }}>Tight</span>
                  <span style={{ fontSize: '0.7rem', fontWeight: 600, minWidth: '2rem' }}>{(clusterThreshold * 100).toFixed(0)}%</span>
                  {clustersQ.isLoading && <span style={{ fontSize: '0.7rem', opacity: 0.5 }}>⏳</span>}
                </div>
              ) }] : []),
              // Deeper clustering tools (embed/reassign/tiered review + the UMAP
              // map) live on their own per-deployment pages — linked here so they
              // stay reachable now that the standalone Explore page is retired.
              ...(groupBy === 'cluster' && activeDeploymentIds.length === 1 ? [{ id: 'cluster-advanced', title: 'Advanced', content: (
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <Link to={`/clusters/${activeDeploymentIds[0]}`} title="Embed, reassign and tiered cluster review"
                    style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--primary)', textDecoration: 'none', whiteSpace: 'nowrap' }}>◧ Review clusters</Link>
                  <Link to={`/umap/${activeDeploymentIds[0]}`} title="2-D embedding map of this deployment"
                    style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--primary)', textDecoration: 'none', whiteSpace: 'nowrap' }}>✦ UMAP</Link>
                </div>
              ) }] : []),
            ],
          },
        ]}
      />

      {/* iNaturalist publish result banner */}
      {inatMsg && (
        <div style={{
          margin: '0 0 1rem', padding: '0.5rem 0.75rem', fontSize: '0.8125rem',
          border: '1px solid var(--border)', borderRadius: 'var(--radius)',
          backgroundColor: inatMsg.startsWith('⚠') ? 'rgba(239,68,68,0.08)' : 'rgba(116,172,0,0.1)',
          display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center',
        }}>
          <span>{inatMsg}</span>
          <button onClick={() => setInatMsg(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', opacity: 0.6 }}>✕</button>
        </div>
      )}

      {loading && <p style={{ opacity: 0.6 }}>Loading media…</p>}
      {error && <p style={{ color: 'var(--error)' }}>⚠ {error}</p>}
      {notice && (
        <p style={{
          margin: '0 0 0.5rem', padding: '0.4rem 0.75rem', fontSize: '0.8rem',
          color: 'var(--text-color)', background: 'rgba(76,175,80,0.12)',
          border: '1px solid rgba(76,175,80,0.35)', borderRadius: 'var(--radius)',
        }}>✓ {notice}</p>
      )}

      {/* ── Not-hosted notice ──────────────────────────────────────── */}
      {/* Keyed on the loaded rows, not the loading flag: the 4 s refetch during an upload sets
          loading on every poll, which blinked the old banner on and off. Optimistic cards are
          not counted (see stats), so an upload in progress no longer triggers it either. */}
      {stats.noImage > 0 && (
        <p style={{
          margin: '0 0 1rem',
          padding: '0.6rem 0.875rem',
          border: '1px solid var(--warning, #f59e0b)',
          borderRadius: 'var(--radius)',
          backgroundColor: 'rgba(245,158,11,0.06)',
          fontSize: '0.8125rem',
        }}>
          📷 {stats.noImage === stats.total ? 'These images are' : `${stats.noImage} of these images are`} not
          hosted online, so only their records can be shown. Upload the originals from the Upload page to
          attach them, or set a hosted URL in the media detail panel.
        </p>
      )}

      {/* ── Thumbnail grid (full width) — selecting a photo opens the modal ── */}
      <div>
        <div>
          {similarToId ? (
            <>
              {/* Find-similar mode: ranked neighbours of the anchor image */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap',
                margin: '0 0 0.625rem', padding: '0.4rem 0.75rem', fontSize: '0.8rem',
                background: 'rgba(33,150,243,0.10)', border: '1px solid rgba(33,150,243,0.35)',
                borderRadius: 'var(--radius)',
              }}>
                <span>🔎 <strong>{Math.max(0, similarRecords.length - 1)}</strong> images similar to the selected one · ranked by visual similarity (anchor shown first)</span>
                <button
                  type="button"
                  onClick={() => setSimilarToId(null)}
                  style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.8rem' }}
                >
                  ✕ Clear
                </button>
              </div>
              {similarLoading && similarRecords.length === 0 && (
                <p style={{ opacity: 0.6, padding: '1rem 0' }}>Searching for similar images…</p>
              )}
              {similarQ.isError && (
                <p style={{ color: 'var(--error)', padding: '0.5rem 0' }}>
                  No embedding for this image yet — run clustering/embedding for this deployment first.
                </p>
              )}
              <div style={{
                display: 'grid',
                gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`,
                gap: thumbScale < 120 ? '0.5rem' : '0.75rem',
              }}>
                {similarRecords.map(m => renderThumbCard(m))}
              </div>
            </>
          ) : (
          <>
          {!loading && filtered.length === 0 && (
            <p style={{ opacity: 0.6, padding: '1rem 0' }}>No media records found for the selected filters.</p>
          )}
          {!loading && filtered.length > 0 && imageView === 'crop' && cropCardCount === 0 && (
            <p style={{ opacity: 0.6, padding: '1rem 0' }}>
              No labels to show here — neither AI detections nor human labels exist for this view.
              Switch to <strong>🖼 Photo</strong> to see and annotate the full frames.
            </p>
          )}
          {/* Grouped view — collapsible sections */}
          {groupedMedia ? groupedMedia.map(([groupLabel, groupItems]) => {
            const clusterInfo = clusterByLabel.get(groupLabel)
            return (
            <MediaGroup
              key={groupLabel}
              label={groupLabel}
              count={groupItems.length}
              action={clusterInfo && !clusterInfo.confirmed && activeDeploymentIds.length === 1
                ? <ClusterLabelAll clusterId={clusterInfo.id} deploymentId={filterDeployments.length === 1 ? filterDeployments[0] : undefined} onDone={onClusterConfirmed} />
                : undefined}
            >
              <div style={{
                display: 'grid',
                gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`,
                gap: thumbScale < 120 ? '0.5rem' : '0.75rem',
              }}>
                {imageView === 'crop' ? groupItems.flatMap(m => cropCardsFor(m)) : groupItems.map(m => renderThumbCard(m))}
              </div>
            </MediaGroup>
            )
          }) : (
          /* Flat (ungrouped) grid */
          <div style={{
            display: 'grid',
            gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`,
            gap: thumbScale < 120 ? '0.5rem' : '0.75rem',
          }}>
            {imageView === 'crop' ? filtered.flatMap(m => cropCardsFor(m)) : filtered.map(m => renderThumbCard(m))}
          </div>
          )}
          </>
          )}

        </div>

        {/* Full-screen labeling modal */}
        {selectedMedia && (
          <MediaDetail
            media={selectedMedia}
            timezone={tzByDeployment.get(selectedMedia.deployment_id)}
            mediaList={filtered}
            onSelect={setSelectedMediaId}
            onClose={() => setSelectedMediaId(null)}
            onUpdated={handleMediaUpdated}
            onNext={advanceToNext}
            onPrev={advanceToPrev}
            focusObsId={focusObsId}
          />
        )}
      </div>

      {/* ── Pagination (WS5-T2) ──────────────────────────────────── */}
      {totalCount !== null && totalCount > PAGE_SIZE && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          gap: '0.5rem', marginTop: '1.25rem', fontSize: '0.8125rem',
        }}>
          <button
            onClick={() => setPage(0)}
            disabled={page === 0}
            style={PAGE_BTN(page === 0)}
          >
            ««
          </button>
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            style={PAGE_BTN(page === 0)}
          >
            ‹ Prev
          </button>
          <span style={{ opacity: 0.7, minWidth: '7rem', textAlign: 'center' }}>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, totalCount)} of {totalCount}
          </span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={(page + 1) * PAGE_SIZE >= totalCount}
            style={PAGE_BTN((page + 1) * PAGE_SIZE >= totalCount)}
          >
            Next ›
          </button>
          <button
            onClick={() => setPage(Math.ceil(totalCount / PAGE_SIZE) - 1)}
            disabled={(page + 1) * PAGE_SIZE >= totalCount}
            style={PAGE_BTN((page + 1) * PAGE_SIZE >= totalCount)}
          >
            »»
          </button>
        </div>
      )}

      {/* ── Advanced settings modal (WS5-T3) ─────────────────────── */}
      <Modal
        open={advancedOpen}
        onClose={() => setAdvancedOpen(false)}
        title="Advanced filters"
        size="sm"
        footer={
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button
              onClick={clearAdvanced}
              style={{
                padding: '0.5rem 1rem', fontSize: '0.875rem',
                border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
              }}
            >
              Clear all
            </button>
            <button
              onClick={() => setAdvancedOpen(false)}
              style={{
                padding: '0.5rem 1.25rem', fontSize: '0.875rem',
                border: 'none', borderRadius: 'var(--radius)',
                background: 'var(--primary)', color: '#fff', cursor: 'pointer',
              }}
            >
              Apply
            </button>
          </div>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

          {/* Observation attribute filters (moved out of the main ribbon) */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Annotation</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={filterStatus} onChange={setFilterStatus} placeholder="Any status"
                options={[
                  { value: 'ai',       label: 'AI identified' },
                  { value: 'reviewed', label: '✓ Reviewed'    },
                  { value: 'pending',  label: '⧗ Processing'  },
                  { value: 'issue',    label: '✕ Issue'       },
                ]} />
              <FilterSelect value={filterAnnotationType} onChange={setFilterAnnotationType} placeholder="Any type"
                options={[
                  { value: 'bbox',  label: '▣ Bounding box' },
                  { value: 'whole', label: '▢ Whole image'  },
                ]} />
              <FilterSelect value={filterAnnotator} onChange={setFilterAnnotator} placeholder="Any annotator"
                options={humanAnnotatorList.map(a => ({ value: a, label: a }))} />
              <FilterSelect value={filterModel} onChange={setFilterModel} placeholder="Any AI model"
                options={modelList} />
            </div>
          </section>

          {/* Animal attributes */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Animal</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={filterSex} onChange={setFilterSex} placeholder="Any sex"
                options={sexList.map(s => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) }))} />
              <FilterSelect value={filterLifeStage} onChange={setFilterLifeStage} placeholder="Any life stage"
                options={lifeStageList.map(s => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) }))} />
            </div>
          </section>

          {/* Date range */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Date range</div>
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                <span style={{ opacity: 0.7 }}>From</span>
                <input
                  type="date"
                  value={filterDateFrom}
                  onChange={e => setFilterDateFrom(e.target.value)}
                  style={{
                    padding: '0.375rem 0.5rem', borderRadius: 'var(--radius)',
                    border: '1px solid var(--border)', backgroundColor: 'var(--surface)',
                    color: 'var(--text-color)', fontSize: '0.8125rem',
                  }}
                />
              </label>
              <label style={{ fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                <span style={{ opacity: 0.7 }}>To</span>
                <input
                  type="date"
                  value={filterDateTo}
                  onChange={e => setFilterDateTo(e.target.value)}
                  style={{
                    padding: '0.375rem 0.5rem', borderRadius: 'var(--radius)',
                    border: '1px solid var(--border)', backgroundColor: 'var(--surface)',
                    color: 'var(--text-color)', fontSize: '0.8125rem',
                  }}
                />
              </label>
            </div>
          </section>

          {/* Time of day */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Time of day</div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {([['all', 'All'], ['day', '☀ Day (06–18)'], ['night', '🌙 Night (18–06)']] as [TimeOfDay, string][]).map(([val, lbl]) => (
                <button
                  key={val}
                  onClick={() => setFilterTime(val)}
                  style={{
                    padding: '0.375rem 0.875rem', fontSize: '0.8125rem',
                    border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                    cursor: 'pointer',
                    backgroundColor: filterTime === val ? 'var(--primary)' : 'transparent',
                    color: filterTime === val ? '#fff' : 'var(--text-color)',
                    transition: 'background-color 0.15s',
                  }}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </section>

        </div>
      </Modal>

      {/* ── Bulk action modals ──────────────────────────────────── */}
      {showDeleteModal && (
        <DeleteConfirmModal
          mediaIds={Array.from(selectedIds)}
          fileNames={filtered.filter(m => selectedIds.has(m.id)).map(m => m.file_name || m.file_path.split('/').pop() || m.id)}
          onConfirm={handleBatchDelete}
          onClose={() => setShowDeleteModal(false)}
        />
      )}
      {showAiPicker && (
        <AiModelPickerModal
          count={selectedIds.size}
          onRun={handleRunAi}
          onClose={() => setShowAiPicker(false)}
        />
      )}
      {showLabelModal && (
        <BulkLabelModal
          count={selectedIds.size}
          busy={labelBusy}
          onApply={bulkLabel}
          onClose={() => setShowLabelModal(false)}
        />
      )}
      {showTrainModal && (() => {
        // The selection as the trainer will see it (optimistic upload placeholders are not rows yet).
        const rows = filtered.filter(m => selectedIds.has(m.id) && !m._pending)
        const depIds = new Set(rows.map(m => m.deployment_id))
        const projectIds = [...new Set(deployments.filter(d => depIds.has(d.id)).map(d => d.project_id))]
        return (
          <TrainModelModal
            media={rows}
            projectIds={projectIds}
            onClose={() => setShowTrainModal(false)}
          />
        )
      })()}
      {pipelineLogs !== null && (
        <PipelineLogModal
          isRunning={pipelineLogs.length > 0 && !pipelineLogs[pipelineLogs.length - 1]?.startsWith('✓') && !pipelineLogs[pipelineLogs.length - 1]?.startsWith('⚠')}
          logs={pipelineLogs}
          onClose={() => setPipelineLogs(null)}
        />
      )}
    </div>
  )
}

