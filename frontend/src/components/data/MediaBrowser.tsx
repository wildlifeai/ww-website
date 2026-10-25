 
import { useState, useEffect, useEffectEvent, useMemo, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  ChartColumn, Check, ChevronLeft, ChevronRight, ChevronsDownUp, ChevronsUpDown, Hourglass, ImageOff, RefreshCw,
  Search, SlidersHorizontal, Upload, X,
} from 'lucide-react'
import { survivingPending } from '../../lib/pendingCards'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { useINat } from '../../hooks/useINat'
import { useProjectRoles } from '../../hooks/useProjectRoles'
import { INatBadge, type INatState } from './INatBadge'
import { MediaDetail } from './MediaDetail'
import { FilterSelect } from '../ui/ControlBar'
import { MultiSelect } from '../ui/MultiSelect'
import { Ribbon } from '../ui/Ribbon'
import { Banner, BannerProgress, type BannerTone } from '../ui/Banner'
import { StatusBadge } from '../ui/StatusBadge'
import { deriveAnnotationStatus } from '../../lib/annotationStatus'
import { Modal } from '../ui/Modal'
import { isHumanReviewed, isAiLabel, photoVerdict, observationLabel } from '../../lib/observations'
import { apiClient } from '../../lib/apiClient'
import { mediaDeleteOutcome, type MediaDeleteResponse } from '../../lib/mediaDelete'
import { showErrorToast, showUndoToast } from '../common/undoToastBus'
import { BulkLabelModal } from './BulkLabelModal'
import { type SpeciesSelection } from './SpeciesPicker'
import { formatCaptureTime } from '../../lib/time'
import { useQueryClient } from '@tanstack/react-query'
import { MediaGroup } from './MediaGroup'
import { ClusterLabelAll } from './ClusterLabelAll'
import { useClusters, useMultiClusters, useSimilarImages } from '../../hooks/useBrain'
import { useFeatureEnabled } from '../../hooks/useFeatureEnabled'
import { useUploadStore } from '../../contexts/UploadContext'
import { useJobsList } from '../../hooks/useJobs'
import { useBusyDeployments } from '../../hooks/useBusyDeployments'
import { useDeploymentFilterOptions } from '../../hooks/useDeploymentFilterOptions'
import { useGridFilterOptions, modelOptionLabel } from '../../hooks/useGridFilterOptions'
import { useAutoRetryThumbnails } from '../../hooks/useAutoRetryThumbnails'
import { useRefreshWhileBusy } from '../../hooks/useRefreshWhileBusy'
import { isThumbnailStuck } from '../../lib/thumbnailRetry'
import { displayImageUrl } from '../../lib/mediaImageUrl'
import { applySelectIntent, arrowTarget, cardClickIntent, cardKeyIntent, circleClickIntent, type CardIntent } from '../../lib/cardSelection'
import { writeVerdicts, type VerdictLabel } from '../../lib/humanVerdict'
import { labelledSpecies } from '../../lib/speciesSuggestions'
import {
  applyFilterOps, applyOrder, gridFilterPlan, gridQueryKey, gridSortPlan, hasAdvancedFilters, hasAnyFilter, NO_FILTERS,
  type GridFilters, type GridSort,
} from '../../lib/gridFilters'
import {
  DEPLOYMENT_GROUP_LIMIT, GROUP_LABELS, availableGroupings, groupInOrder, observationGroupKey, type GroupBy,
} from '../../lib/gridGrouping'
import { PAGE_SIZE, pageWindow } from '../../lib/gridPage'
import { MediaBulkActions, type BulkAction } from './MediaBulkActions'
import { DeleteConfirmModal, AiModelPickerModal, PipelineLogModal } from './BulkActionModals'
import { TrainModelModal } from './TrainModelModal'

// Columns fetched for a media record (+ its assets and observations). Shared by
// the paginated grid loader and the "find similar" fetch so they stay in sync.
const MEDIA_SELECT =
  'id, deployment_id, file_path, file_name, file_mediatype, timestamp, created_at, file_public, media_comments, exif_metadata, ' +
  'media_assets(thumbnail_url, preview_url, animal_crop_url), ' +
  'observations(id, deployment_id, media_id, observation_type, scientific_name, vernacular_name, taxon_id, ' +
  'count, life_stage, sex, behavior, ' +
  'classification_method, classified_by, classification_timestamp, classification_probability, observation_comments, crop_url, ' +
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
  classification_timestamp?: string | null
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
  deployments: { id: string; location_name: string | null; project_id: string; timezone?: string | null; deployment_start?: string | null }[]
  /** WS5-T6: Pre-select a deployment when navigating from the upload dock. */
  initialDeploymentId?: string
  /** Pre-apply a species filter (e.g. deep-linked from an Insights chart). */
  initialSpecies?: string
  /** Open this photo in the viewer (e.g. linked from the UMAP explorer). */
  initialMediaId?: string
}

// A thin divider that separates the purpose-groups in the ribbon's first row
// (pager │ display controls │ selection).
const RIBBON_SECTION: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  borderLeft: '1px solid var(--border)', paddingLeft: '0.7rem', marginLeft: '0.7rem',
}

const SMALL_BTN: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', padding: '0.2rem 0.5rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent',
  color: 'var(--primary)', cursor: 'pointer', whiteSpace: 'nowrap', textDecoration: 'none',
}

const ELLIPSIS: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }
const TWO_LINES: React.CSSProperties = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }

// The fixed values the observations table allows (ww-backend CHECK constraints).
const SEX_VALUES = ['female', 'male', 'unknown']
const LIFE_STAGE_VALUES = ['adult', 'subadult', 'juvenile', 'hatchling', 'unknown']
const capitalised = (s: string) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) })

/** Labels view: at most this many cards per photo; the viewer shows the rest. */
const MAX_LABEL_CARDS = 4

const EMPTY_IDS: ReadonlySet<string> = new Set()

/** The advanced filters cleared, keeping the species choice. */
const clearedAdvanced = (f: GridFilters): GridFilters => ({ ...NO_FILTERS, species: f.species })

const pagerButton = (disabled: boolean): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', padding: '0.15rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'transparent', cursor: disabled ? 'not-allowed' : 'pointer',
  color: disabled ? 'var(--text-muted)' : 'var(--primary)',
})

const cardStyle = (sel: boolean, isOpen: boolean): React.CSSProperties => ({
  border: sel || isOpen ? '2px solid var(--primary)' : '1px solid var(--border)',
  borderRadius: 'var(--radius)', overflow: 'hidden', cursor: 'pointer',
  backgroundColor: 'var(--surface)', transition: 'border-color 0.15s, transform 0.15s',
  transform: isOpen ? 'scale(1.02)' : undefined, userSelect: 'none',
})

// Every card keeps a 4:3 image area whatever the column width (02 #20).
const IMAGE_AREA: React.CSSProperties = {
  aspectRatio: '4 / 3', display: 'flex', alignItems: 'center', justifyContent: 'center',
  overflow: 'hidden', position: 'relative',
}

/** Arrow keys move focus between the grid's cards, a row at a time up and down. */
function moveFocus(e: React.KeyboardEvent<HTMLElement>): boolean {
  const cards = Array.from(document.querySelectorAll<HTMLElement>('.media-card'))
  const grid = e.currentTarget.parentElement
  const columns = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length : 1
  const next = arrowTarget(e.key, cards.indexOf(e.currentTarget), cards.length, columns)
  if (next === null) return false
  cards[next].focus()
  return true
}

// Which observations get a card in the "Labels" view: AI detections that have a
// crop, plus any human-made or human-reviewed label (these usually have no crop,
// so they're shown on the full frame). AI detections with no crop are omitted.
function isLabelCard(o: ObservationRecord): boolean {
  return !!o.crop_url || o.source_type === 'human' || isHumanReviewed(o)
}

const labelCards = (m: MediaRecord) => m.observations.filter(isLabelCard).slice(0, MAX_LABEL_CARDS)

/** Media records for `ids`, in that order (deleted photos dropped), 100 ids per request. */
async function fetchMediaInOrder(ids: readonly string[]): Promise<MediaRecord[]> {
  const rank = new Map(ids.map((id, i) => [id, i]))
  const chunks = Array.from({ length: Math.ceil(ids.length / 100) }, (_, i) => ids.slice(i * 100, (i + 1) * 100))
  const pages = await Promise.all(chunks.map(async chunk => {
    const { data, error } = await supabase.from('media').select(MEDIA_SELECT).in('id', chunk).is('deleted_at', null)
    if (error) throw new Error(error.message)
    return (data || []) as unknown as MediaRecord[]
  }))
  return pages.flat().sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0))
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
  // Insights takes the deployment parameter once per deployment.
  const insightsLink = `/insights?tab=reports${depIds.map(id => `&deployment=${encodeURIComponent(id)}`).join('')}`

  return (
    <Banner
      tone="info"
      icon={RefreshCw}
      action={
        <Link to={insightsLink} style={{ ...SMALL_BTN, border: 'none', fontWeight: 600, fontSize: '0.8125rem' }}
          title="See species and detection numbers for these photos as they are analysed">
          <ChartColumn size={14} aria-hidden /> See stats so far
        </Link>
      }
      footer={<BannerProgress pct={pct} />}
    >
      <strong>Analysing {pct}%</strong>: the AI is processing {isOne ? 'deployment ' : 'deployments '}
      <strong>{shown}</strong>. Thumbnails and labels appear as they complete.
    </Banner>
  )
}

// ── Component ────────────────────────────────────────────────────────────────

// Already far over the 300-line limit on dev; splitting it is its own change.
// react-doctor-disable-next-line react-doctor/no-giant-component
export function MediaBrowser({ deployments, initialDeploymentId, initialSpecies, initialMediaId }: Props) {
  const { user } = useAuth()
  const roles = useProjectRoles()
  // deployment_id → IANA timezone, so capture times render in the photo's local zone.
  const tzByDeployment = useMemo(
    () => new Map(deployments.map(d => [d.id, d.timezone ?? null])),
    [deployments],
  )
  const projectByDeployment = useMemo(() => new Map(deployments.map(d => [d.id, d.project_id])), [deployments])
  const deploymentOptions = useDeploymentFilterOptions(deployments)
  const qc = useQueryClient()
  const { isActive: uploadActive, pendingUploads, pendingSince } = useUploadStore()
  const [reloadKey, setReloadKey] = useState(0)
  // Set by the background refresh during a job: that fetch keeps the cards on screen (#286).
  const silentReload = useRef(false)
  // Set when rows may have joined or left the filtered set, so the next fetch counts again.
  const recount = useRef(false)
  const reload = useCallback((countAgain: boolean) => {
    if (countAgain) recount.current = true
    setReloadKey(k => k + 1)
  }, [])
  // Media whose thumbnail failed to load — shown as "processing" (the rendition
  // is likely still generating). Reset on every (re)load so they re-attempt.
  const [failedThumbs, setFailedThumbs] = useState<Set<string>>(new Set())
  // When the current page of media arrived: the "now" for deciding a missing thumbnail is
  // stuck rather than still on its way (#208), kept out of render so it stays pure.
  const [loadedAt, setLoadedAt] = useState(0)
  // Deployments whose thumbnail backfill this page started, until the job list shows it.
  const [retryRequested, setRetryRequested] = useState<Set<string>>(new Set())
  const [media, setMedia]         = useState<MediaRecord[]>([])
  // "Find similar" mode: anchor media id + the resolved, similarity-ranked records.
  const [similarToId, setSimilarToId]     = useState<string | null>(null)
  const [similarRecords, setSimilarRecords] = useState<MediaRecord[]>([])
  const [similarLoading, setSimilarLoading] = useState(false)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  // One notice at a time (a result, a failure, the iNaturalist upload), shown as a Banner.
  const [notice, setNotice]       = useState<{ tone: BannerTone; text: string } | null>(null)
  const flash = useCallback((tone: BannerTone, text: string, autoHide = tone === 'success') => {
    setNotice({ tone, text })
    if (autoHide) window.setTimeout(() => setNotice(n => (n?.text === text ? null : n)), 4000)
  }, [])
  const [selectedMediaId, setSelectedMediaId] = useState<string | null>(null)
  // The observation a crop card was opened from, pre-selected in the detail.
  const [focusObsId, setFocusObsId] = useState<string | null>(null)

  // ── Filters (all run in the media query, lib/gridFilters) ────────────────
  const [filterDeployments, setFilterDeployments] = useState<string[]>([])
  const [filters, setFilters] = useState<GridFilters>(() => ({ ...NO_FILTERS, species: initialSpecies ? [initialSpecies] : [] }))
  // The Advanced filters dialog edits a draft, applied in one query on Apply.
  const [draft, setDraft] = useState<GridFilters | null>(null)
  const [wantSpeciesOptions, setWantSpeciesOptions] = useState(false)
  const [thumbScale, setThumbScale]             = useState(() => {
    const saved = localStorage.getItem('ww:thumbScale')
    return saved ? Number(saved) : 140
  })
  // Grouping is not remembered: a returning reviewer starts on the plain grid (02 #12).
  const [groupChoice, setGroupChoice]           = useState<GroupBy>('none')
  // Photo (full frame) vs crop (the detected animal cut-out). Crops make it fast
  // to scan a species for misclassifications; photos reveal animals missing a box.
  const [imageView, setImageView]               = useState<'photo' | 'crop'>(() => {
    return localStorage.getItem('ww:imageView') === 'crop' ? 'crop' : 'photo'
  })
  const [clusterThreshold, setClusterThreshold] = useState(0.0)

  // Persist thumbScale and imageView to localStorage
  useEffect(() => { localStorage.setItem('ww:thumbScale', String(thumbScale)) }, [thumbScale])
  useEffect(() => { localStorage.setItem('ww:imageView', imageView) }, [imageView])

  // The deployments the query reads, keyed by value so a new array with the same ids (the
  // timezone fill, a parent re-render) does not refetch or reset the page.
  const deploymentKey = (filterDeployments.length ? filterDeployments : deployments.map(d => d.id)).join(',')
  const activeDeploymentIds = useMemo(() => (deploymentKey ? deploymentKey.split(',') : []), [deploymentKey])
  const queryKey = gridQueryKey(activeDeploymentIds, filters)
  const plan = useMemo(() => gridFilterPlan(filters), [filters])
  const singleDeployment = activeDeploymentIds.length === 1 ? activeDeploymentIds[0] : null
  // Clusters, UMAP and the active-learning order exist only where their backend flag is on
  // (decision 6); the flags are global, so any deployment in view can be asked.
  const brainOn = useFeatureEnabled('brain', activeDeploymentIds[0] ?? null)
  const reviewOrderOn = useFeatureEnabled('activeLearning', activeDeploymentIds[0] ?? null)
  const groupOptions = availableGroupings(activeDeploymentIds.length).filter(g => g !== 'cluster' || brainOn)
  const groupBy: GroupBy = groupOptions.includes(groupChoice) ? groupChoice : 'none'
  const filterOptions = useGridFilterOptions(activeDeploymentIds, wantSpeciesOptions || draft !== null)
  // "Needs review first": the retired Review Queue's order (decision 6), a sort of the media
  // query, so every filter and the pager apply (lib/gridFilters, ww-backend #337).
  const [orderBy, setOrderBy] = useState<'' | 'review'>('')
  const sort: GridSort = orderBy === 'review' && reviewOrderOn ? 'review' : 'newest'

  // Page and selection belong to one query: a new filter or order goes back to page 1, and a
  // new page (or the similar list) starts with nothing selected ("All on page" means what is
  // on screen).
  const listKey = `${queryKey}|${sort}`
  const [pageAt, setPageAt] = useState({ key: '', page: 0 })
  const page = pageAt.key === listKey ? pageAt.page : 0
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const pageKey = `${listKey}|${page}|${similarToId ?? ''}`
  const [selection, setSelection] = useState<{ key: string; ids: ReadonlySet<string> }>({ key: '', ids: EMPTY_IDS })
  const selectedIds = selection.key === pageKey ? selection.ids : EMPTY_IDS
  const setSelectedIds = (ids: ReadonlySet<string>) => setSelection({ key: pageKey, ids })
  const clearSelection = useCallback(() => setSelection({ key: '', ids: EMPTY_IDS }), [])
  // The last card selected, where a Shift-click range starts.
  const selectAnchor = useRef<string | null>(null)
  const goToPage = (p: number) => {
    setPageAt({ key: listKey, page: Math.max(0, p) })
    window.scrollTo({ top: 0 })
  }

  // Groups the reviewer collapsed, for the current grouping.
  const [collapsedAt, setCollapsedAt] = useState<{ groupBy: GroupBy; labels: ReadonlySet<string> }>({ groupBy: 'none', labels: EMPTY_IDS })
  const collapsed = collapsedAt.groupBy === groupBy ? collapsedAt.labels : EMPTY_IDS

  // ── Roles (decision 1): only people who can write to a photo's project select it ──
  const canWriteDeployment = (deploymentId: string) => {
    const project = projectByDeployment.get(deploymentId)
    return !roles.loading && !!project && roles.canWrite(project)
  }
  const isSelectable = (m: MediaRecord) => !m._pending && canWriteDeployment(m.deployment_id)

  // ── Phase 3: iNaturalist publish + tracking ───────────────────────────────
  const inat = useINat()
  const [, setInatMode]         = useState(false)            // iNat selection mode
  const [inatSelected, setInatSelected] = useState<Set<string>>(new Set())
  const [inatStates, setInatStates]     = useState<Map<string, { state: INatState; uri: string | null }>>(new Map())
  const [inatBusy, setInatBusy]         = useState(false)

  const [showDeleteModal, setShowDeleteModal]   = useState(false)
  const [showAiPicker, setShowAiPicker]         = useState(false)
  const [showLabelModal, setShowLabelModal]     = useState(false)
  const [showTrainModal, setShowTrainModal]     = useState(false)
  const [labelBusy, setLabelBusy]               = useState(false)
  const [pipelineLogs, setPipelineLogs]         = useState<string[] | null>(null)

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

  const handleBulkAction = useCallback(async (action: BulkAction) => {
    if (action === 'similar') {
      // Anchor on the single selected image and enter similarity-ranked mode.
      const anchor = [...selectedIds][0]
      if (anchor) { setSimilarToId(anchor); clearSelection() }
    } else if (action === 'delete') {
      setShowDeleteModal(true)
    } else if (action === 'ai') {
      setShowAiPicker(true)
    } else if (action === 'label') {
      setShowLabelModal(true)
    } else if (action === 'train') {
      setShowTrainModal(true)
    } else if (action === 'inat') {
      // Not connected, the menu links to the Toolkit page instead of offering this.
      // Upload the unified selection. Pass the ids directly: setInatSelected is
      // async, so uploadToInat() reading inatSelected here would see the stale
      // (empty) set and silently no-op.
      setInatSelected(new Set(selectedIds))
      setInatMode(true)
      uploadToInat(new Set(selectedIds))
    }
    // syncFromInat/uploadToInat are plain (unmemoised) functions declared below;
    // including them would just defeat the memo without changing behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds, clearSelection])

  const handleBatchDelete = useCallback(async (ids: string[]) => {
    const res = await apiClient.del('/api/media/batch', { media_ids: ids }) as { data?: MediaDeleteResponse }
    const { deletedIds, deletedAt, message } = mediaDeleteOutcome(ids, res?.data)
    // Nothing deleted: throw so the confirm modal stays open and shows why.
    if (deletedIds.length === 0) throw new Error(message)
    const gone = new Set(deletedIds)
    // Capture the rows so Undo can re-insert them without a full refetch.
    const removed = media.filter(m => gone.has(m.id))
    setMedia(prev => prev.filter(m => !gone.has(m.id)))
    setSimilarRecords(prev => prev.filter(m => !gone.has(m.id)))
    setTotalCount(t => (t === null ? t : Math.max(0, t - deletedIds.length)))
    clearSelection()
    if (deletedAt) {
      showUndoToast({
        message,
        onUndo: async () => {
          await apiClient.post('/api/media/batch/restore', { media_ids: deletedIds, deleted_at: deletedAt })
          setMedia(prev => {
            const have = new Set(prev.map(m => m.id))
            return [...removed.filter(m => !have.has(m.id)), ...prev]
          })
          setTotalCount(t => (t === null ? t : t + deletedIds.length))
        },
      })
    }
  }, [media, clearSelection])

  const handleRunAi = useCallback(async (models: string[]) => {
    const ids = Array.from(selectedIds)
    setPipelineLogs([`Starting AI pipeline for ${ids.length} images with models: ${models.join(', ')}…`])
    try {
      // Through apiClient: the API's own origin and the session token. A bare relative fetch went
      // to the site, not the API, and carried no token.
      const res = await apiClient.post('/api/media/run-selected', { media_ids: ids, steps: models }) as {
        data?: { job_id?: string }
      }
      setPipelineLogs(prev => [...(prev || []), `Job queued: ${res.data?.job_id ?? 'unknown'}`])
    } catch (e) {
      setPipelineLogs(prev => [...(prev || []), `Error: ${e instanceof Error ? e.message : 'unknown'}`])
    }
  }, [selectedIds])

  // Honour ?deployment=<id>[,<id>...] from the upload redirect and the dock link. One id focuses
  // that deployment; several (an upload that spanned deployments) select them all, and the
  // deployment pill reads "N deployments". A new deployment choice starts on page 1 by itself.
  useEffect(() => {
    if (!initialDeploymentId) return
    const ids = initialDeploymentId.split(',').map(s => s.trim()).filter(Boolean)
    if (ids.length) setFilterDeployments(ids)
  }, [initialDeploymentId])

  // ── Fetch media (with pagination) ─────────────────────────────────────────
  // Every filter is part of this query, so the page and the total agree. The total is counted
  // once per filter set (and again after a write), not on every page turn or refresh (02 #7).
  const countedFor = useRef<string | null>(null)
  useEffect(() => {
    const silent = silentReload.current
    silentReload.current = false
    if (!user) return

    if (activeDeploymentIds.length === 0) {
      setMedia([])
      setTotalCount(0)
      return
    }

    let cancelled = false
    if (!silent) setLoading(true)
    setError(null)

    const from = page * PAGE_SIZE
    const to   = from + PAGE_SIZE - 1
    const wantCount = countedFor.current !== queryKey || recount.current
    const order = gridSortPlan(sort)

    const query = supabase
      .from('media')
      .select([MEDIA_SELECT, ...plan.embeds, ...order.embeds].join(', '), wantCount ? { count: 'exact' } : undefined)
      .in('deployment_id', activeDeploymentIds)
      .is('deleted_at', null)
    applyOrder(applyFilterOps(query, plan.ops), order.orders)
      .range(from, to)
      .then(({ data, error: err, count }) => {
        if (cancelled) return
        if (err) { setError(err.message); setLoading(false); return }
        setMedia((data || []) as unknown as MediaRecord[])
        if (wantCount) {
          countedFor.current = queryKey
          recount.current = false
          setTotalCount(count ?? null)
        }
        setFailedThumbs(new Set())  // re-attempt thumbnails (renditions may now exist)
        setLoadedAt(Date.now())
        setLoading(false)
      })

    return () => { cancelled = true }
  }, [user, activeDeploymentIds, queryKey, plan, sort, page, reloadKey])

  // While an upload is running, poll so newly-registered media appear in the grid
  // without a manual refresh, and do one final refresh when it finishes (cleanup
  // runs on the isActive → false transition, catching the last batch), counted again.
  useEffect(() => {
    if (!uploadActive) return
    const t = setInterval(() => setReloadKey(k => k + 1), 4000)
    return () => { clearInterval(t); reload(true) }
  }, [uploadActive, reload])

  // Deployments with a queued or running job. When one finishes, reload so the
  // thumbnails and labels it produced appear without a manual refresh.
  const busy = useBusyDeployments(() => {
    setRetryRequested(new Set())
    reload(true)
  })

  const retryThumbnails = useCallback(async (deploymentId: string) => {
    setRetryRequested(s => new Set(s).add(deploymentId))
    try {
      await apiClient.post(`/api/media/thumbnails/${deploymentId}`, {})
      await qc.invalidateQueries({ queryKey: ['jobs'] })
    } catch (e) {
      setRetryRequested(s => { const n = new Set(s); n.delete(deploymentId); return n })
      flash('error', `Couldn't start the thumbnails: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [qc, flash])

  // Cards with no image to show: still processing, or stuck past the grace period (#208).
  const missingThumbs = media.filter(m => !retryRequested.has(m.deployment_id) && (!displayImageUrl(m, 'thumb') || failedThumbs.has(m.id)))
  // Start the backfill once by itself for a deployment whose cards say "No thumbnail" (#175).
  useAutoRetryThumbnails(missingThumbs, loadedAt, busy, retryThumbnails)
  // One notice with one Retry for the stuck deployments on the page, not a button per card (02 #22).
  const stuckThumbs = missingThumbs.filter(m => isThumbnailStuck(m, loadedAt, busy))
  const stuckDeploymentIds = [...new Set(stuckThumbs.map(m => m.deployment_id))]

  // Refresh iNaturalist badges whenever the loaded media set changes.
  useEffect(() => { loadInatStates(media.map(m => m.id)) }, [media, loadInatStates])

  // Upload the iNat-selected media to iNaturalist (burst-consolidated).
  // Accepts an explicit id set so callers can avoid the async-state trap;
  // falls back to inatSelected when called without args.
  const uploadToInat = async (ids?: Set<string>) => {
    const toPublish = ids ?? inatSelected
    if (toPublish.size === 0 || inatBusy) return
    setInatBusy(true)
    flash('info', 'Uploading to iNaturalist…', false)
    try {
      const r = await inat.publish([...toPublish])
      flash('success',
        `${r.observations_created} observation(s), ${r.photos_uploaded} photo(s) on iNaturalist` +
        (r.skipped_bycatch ? ` · ${r.skipped_bycatch} by-catch skipped` : '') +
        (r.skipped_already_published ? ` · ${r.skipped_already_published} already on iNat` : '') +
        (r.errors ? ` · ${r.errors} error(s)` : ''),
        false,
      )
      setInatSelected(new Set())
      await loadInatStates(media.map(m => m.id))
    } catch (e) {
      flash('error', (e as Error)?.message ?? 'Upload failed')
    } finally {
      setInatBusy(false)
    }
  }

  // Bulk "Label as": one human verdict per selected photo, updated when the photo has one, and
  // AI rows untouched (lib/humanVerdict, decision 2), with Undo; a refusal shows in the same toast.
  // iNat community-ID sync lives in Settings.
  const bulkLabel = async (choice: SpeciesSelection | null) => {
    if (labelBusy) return
    const photos = visible.filter(m => selectedIds.has(m.id) && isSelectable(m))
    if (photos.length === 0) { setShowLabelModal(false); return }
    setLabelBusy(true)
    const label: VerdictLabel = choice
      ? { observation_type: 'animal', taxon_id: choice.taxon_id, scientific_name: choice.scientific_name, vernacular_name: choice.vernacular_name }
      : { observation_type: 'blank' }
    try {
      const result = await writeVerdicts(supabase, photos, label, { userId: user?.id, userEmail: user?.email })
      // The cards show the new verdicts at once; the reload then counts again, since a status
      // filter may no longer match them.
      const written = (m: MediaRecord) => {
        const rows = result.observations.get(m.id)
        return rows ? { ...m, observations: rows } : m
      }
      setMedia(prev => prev.map(written))
      setSimilarRecords(prev => prev.map(written))
      clearSelection()
      reload(true)
      const name = choice ? (choice.vernacular_name || choice.scientific_name) : 'empty'
      showUndoToast({
        message: `Labelled ${result.saved} photo${result.saved !== 1 ? 's' : ''} as ${name}`,
        onUndo: async () => {
          await result.undo()
          reload(true)
        },
      })
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : String(e))
    } finally {
      setLabelBusy(false)
      setShowLabelModal(false)
    }
  }

  // ── Page contents ───────────────────────────────────────────────────────────
  // Optimistic cards for files still uploading (no DB row yet), shown instantly via the local
  // preview so the grid isn't empty right after an upload redirect. Scoped to the deployment(s)
  // in view, on the first unfiltered page, and retired one for one as real rows land (see
  // lib/pendingCards for why a name match is not enough).
  const showPending = page === 0 && !hasAnyFilter(filters)
  const pendingCards = useMemo<MediaRecord[]>(() => {
    if (pendingUploads.length === 0 || !showPending) return []
    const viewIds = new Set(activeDeploymentIds)
    return survivingPending(pendingUploads, media, pendingSince, viewIds).map(p => ({
      id: `pending:${p.fileName.toLowerCase()}`, deployment_id: p.deploymentId, file_path: '', file_name: p.fileName,
      file_mediatype: 'image/jpeg', timestamp: null, file_public: false, media_comments: null,
      exif_metadata: null, media_assets: null, observations: [], _pending: true,
    }))
  }, [pendingUploads, pendingSince, media, activeDeploymentIds, showPending])

  // Real (viewable) media first; still-uploading placeholders appended last so the user
  // interacts with available photos rather than "Uploading" cards.
  const filtered = useMemo(() => (pendingCards.length ? [...media, ...pendingCards] : media), [media, pendingCards])
  // The cards on screen: the page, or the similarity results.
  const visible = similarToId ? similarRecords : filtered
  const visibleSelectable = visible.filter(isSelectable)
  const selectAllOnPage = () => setSelectedIds(new Set(visibleSelectable.map(m => m.id)))

  // ── Keyboard shortcuts ──────────────────────────────────────────────────────
  const onWindowKey = useEffectEvent((e: KeyboardEvent) => {
    // Ctrl/Cmd+A: select every selectable photo on the page (once a selection has started)
    if ((e.ctrlKey || e.metaKey) && e.key === 'a' && selectedIds.size > 0) {
      e.preventDefault()
      selectAllOnPage()
    }
    // Escape: clear the selection. While the viewer is open its own Escape closes it, and the
    // selection survives.
    if (e.key === 'Escape' && !selectedMediaId && selectedIds.size > 0) clearSelection()
  })
  useEffect(() => {
    const handler = (e: KeyboardEvent) => onWindowKey(e)
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // ── Cluster data (only fetched when grouped by cluster, one deployment) ─────
  // While a job runs on a deployment in view, refetch the page so its thumbnails appear (#286).
  useRefreshWhileBusy(activeDeploymentIds, busy, () => { silentReload.current = true; setReloadKey(k => k + 1) })
  const clustersQ = useMultiClusters(
    groupBy === 'cluster' ? activeDeploymentIds : [],
    clusterThreshold,
  )
  // Review clusters and UMAP need an embedding run on the deployment.
  const clusterRun = useClusters(groupBy === 'cluster' && singleDeployment ? singleDeployment : undefined)

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
    reload(true)
    flash('success', `Labelled cluster as ${name}${created ? ` · ${created} observation${created !== 1 ? 's' : ''}` : ''}`)
  }, [qc, reload, flash])

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
    fetchMediaInOrder([similarToId, ...hits.map(h => h.media_id)])
      .then(recs => { if (!cancelled) setSimilarRecords(recs) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setSimilarLoading(false) })
    return () => { cancelled = true }
  }, [similarToId, similarQ.data])

  // ── A linked photo (?media=, from the UMAP explorer) opens in the viewer ──
  const userId = user?.id
  const [linkedMedia, setLinkedMedia] = useState<MediaRecord | null>(null)
  useEffect(() => {
    if (!initialMediaId || !userId) return
    let cancelled = false
    fetchMediaInOrder([initialMediaId])
      .then(([rec]) => { if (!cancelled && rec) { setLinkedMedia(rec); setSelectedMediaId(rec.id) } })
      .catch(() => { /* a photo that cannot be read just does not open */ })
    return () => { cancelled = true }
  }, [initialMediaId, userId])

  // ── Grouped media computation ─────────────────────────────────────────────
  const groupedMedia = useMemo(() => {
    if (groupBy === 'none') return null
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

    return groupInOrder(filtered, m => {
      if (groupBy === 'cluster') {
        if (!clusterData) return '(Loading clusters…)'
        if (outlierSet.has(m.id)) return 'Outliers'
        const cid = mediaCluster[m.id]
        // Not in any cluster yet (e.g. blank frame with no animal crop, or
        // embeddings not run for this deployment).
        if (cid == null) return 'Not yet clustered'
        return clusterNames.get(cid) ?? `Cluster ${cid}`
      }
      if (groupBy === 'deployment') return deploymentNames.get(m.deployment_id) || m.deployment_id.slice(0, 8)
      return observationGroupKey(groupBy, m.observations)
    })
  }, [filtered, groupBy, deployments, clustersQ.data])

  // ── Page stats ────────────────────────────────────────────────────────────
  // Over real rows only: an optimistic card has no file_path by construction, and counting it
  // as "no image" (and as media) made the header read "10 no image" during every upload.
  const stats = useMemo(() => {
    const real = filtered.filter(m => !m._pending)
    return {
      total:     real.length,
      uploading: filtered.length - real.length,
      noImage:   real.filter(m => !m.file_path).length,
    }
  }, [filtered])
  const win = pageWindow(page, stats.total, totalCount)

  // Number of cards the Labels view renders this page (AI crops + human labels).
  const cropCardCount = useMemo(() => {
    if (imageView !== 'crop') return 0
    let n = 0
    for (const m of filtered) n += labelCards(m).length
    return n
  }, [filtered, imageView])

  const advancedActive = hasAdvancedFilters(filters)
  const advancedCount = [filters.status, filters.annotator, filters.model, filters.annotationType, filters.sex, filters.lifeStage, filters.dateFrom || filters.dateTo]
    .filter(Boolean).length
  const clearAdvanced = () => setFilters(clearedAdvanced)
  const editDraft = (patch: Partial<GridFilters>) => setDraft(d => (d ? { ...d, ...patch } : d))

  // The viewer steps through the cards on screen; a linked photo opens even when it is not one.
  const selectedMedia = visible.find(m => m.id === selectedMediaId)
    ?? (linkedMedia?.id === selectedMediaId ? linkedMedia : null)

  const handleMediaUpdated = (updated: MediaRecord) => {
    const swap = (list: MediaRecord[]) => list.map(m => m.id === updated.id ? updated : m)
    setMedia(swap)
    setSimilarRecords(swap)
    setLinkedMedia(m => (m?.id === updated.id ? updated : m))
  }

  // AN-4: after a one-click Confirm/Blank, jump to the next image in the grid.
  const advanceToNext = () => {
    if (!selectedMediaId) return
    const idx = visible.findIndex(m => m.id === selectedMediaId)
    const next = idx >= 0 && idx < visible.length - 1 ? visible[idx + 1] : null
    setSelectedMediaId(next ? next.id : null)
  }

  // Step back to the previous image in the grid (modal ‹ arrow).
  const advanceToPrev = () => {
    if (!selectedMediaId) return
    const idx = visible.findIndex(m => m.id === selectedMediaId)
    if (idx > 0) setSelectedMediaId(visible[idx - 1].id)
  }

  if (deployments.length === 0) {
    return <p style={{ color: 'var(--text-muted)', padding: '2rem 0' }}>Select a project or add deployments to browse media.</p>
  }

  const compact = thumbScale < 120
  const roomy = thumbScale >= 200
  const gridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: `repeat(auto-fill, minmax(${thumbScale}px, 1fr))`,
    gap: compact ? '0.5rem' : '0.75rem',
  }

  // ── Card gestures (#283, rules in lib/cardSelection.ts) ───────────────────
  // The grid's selectable media in rendered order, for a Shift-click range.
  const gridOrder = () => {
    if (similarToId) return visible.flatMap(m => (isSelectable(m) ? [m.id] : []))
    const ids: string[] = []
    for (const m of groupedMedia ? groupedMedia.flatMap(([, items]) => items) : filtered) {
      if (isSelectable(m) && (imageView !== 'crop' || m.observations.some(isLabelCard))) ids.push(m.id)
    }
    return ids
  }
  const onCardIntent = (id: string, intent: CardIntent, focusObs: string | null) => {
    if (intent === 'open') { setFocusObsId(focusObs); setSelectedMediaId(id); return }
    const next = applySelectIntent({ selected: selectedIds, anchor: selectAnchor.current }, intent, id, gridOrder())
    selectAnchor.current = next.anchor
    setSelectedIds(next.selected)
  }
  const cardProps = (m: MediaRecord, focusObs: string | null) => {
    const selectable = isSelectable(m)
    return {
      className: 'media-card',
      tabIndex: 0,
      title: selectable ? 'Click to open · Ctrl/Cmd-click or the circle to select · Shift-click to select a range' : 'Click to open',
      // Someone who cannot label these photos opens them with any click.
      onClick: (e: React.MouseEvent) => onCardIntent(m.id, selectable ? cardClickIntent(e) : 'open', focusObs),
      onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
        // The clicked card keeps focus under the viewer, whose keys are its own.
        if (selectedMediaId) return
        const intent = cardKeyIntent(e)
        if (intent && (selectable || intent === 'open')) { e.preventDefault(); onCardIntent(m.id, intent, focusObs) }
        else if (e.target === e.currentTarget && moveFocus(e)) e.preventDefault()
      },
    }
  }
  const renderSelectCircle = (m: MediaRecord, sel: boolean) => isSelectable(m) && (
    <button
      type="button"
      className="media-card-select"
      aria-label={`Select ${m.file_name || m.file_path.split('/').pop() || 'photo'}`}
      aria-pressed={sel}
      onClick={e => { e.stopPropagation(); onCardIntent(m.id, circleClickIntent(e), null) }}
      style={{
        position: 'absolute', bottom: 4, left: 4, width: 18, height: 18, padding: 0, border: 'none',
        borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer',
        backgroundColor: sel ? 'var(--primary)' : 'rgba(0,0,0,0.45)',
        color: '#fff', boxShadow: '0 0 0 1.5px rgba(255,255,255,0.85)',
        // Every circle shows while a selection is active; otherwise on hover (index.css).
        opacity: selectedIds.size > 0 ? 1 : undefined,
      }}
    >
      {sel && <Check size={12} strokeWidth={3} aria-hidden />}
    </button>
  )

  // The card's label line: one name (common name first, as the viewer shows it) and one
  // confidence, the verdict's (02 #17, #18). Hidden on small thumbnails; two lines on large.
  const renderLabelBar = (name: string | null, isEmpty: boolean, conf: number | null, hint: string, timestamp: string | null, deploymentId: string) => {
    if (compact) return null
    return (
      <div style={{ padding: '0.375rem 0.5rem', fontSize: '0.6875rem' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem' }}>
          <span title={hint} style={{ flex: 1, minWidth: 0, fontWeight: 600, ...(roomy ? TWO_LINES : ELLIPSIS) }}>
            {name ?? (isEmpty
              ? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>Empty</span>
              : <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>No label</span>)}
          </span>
          {conf !== null && <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>{(conf * 100).toFixed(0)}%</span>}
        </div>
        {timestamp && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.625rem', ...ELLIPSIS }}>
            {formatCaptureTime(timestamp, tzByDeployment.get(deploymentId))}
          </div>
        )}
      </div>
    )
  }


  // ── Thumbnail card renderer (shared by flat + grouped grids) ──────────────
  const renderThumbCard = (m: MediaRecord) => {
    const imgUrl = displayImageUrl(m, 'thumb')
    const fileName = m.file_name || m.file_path.split('/').pop() || ''

    // Optimistic (still-uploading) card — display-only, no selection/detail/actions.
    if (m._pending) {
      return (
        <div key={m.id} title={`Uploading ${fileName}`} style={{
          border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden',
          backgroundColor: 'var(--surface)',
        }}>
          <div style={{ ...IMAGE_AREA, backgroundColor: 'var(--surface)' }}>
            {imgUrl ? (
              <img src={imgUrl} alt={fileName} decoding="async" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Uploading…</span>
            )}
            <span style={{ position: 'absolute', top: 6, left: 6, display: 'inline-flex', alignItems: 'center', gap: 3, background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '0.62rem', padding: '2px 6px', borderRadius: 4 }}>
              <Upload size={10} aria-hidden /> Uploading
            </span>
          </div>
          {!compact && <div style={{ padding: '0.4rem 0.5rem', fontSize: '0.68rem', color: 'var(--text-muted)', ...ELLIPSIS }}>{fileName}</div>}
        </div>
      )
    }

    // WS5-T4 / AN-2: derive status badge from the review_status contract
    const annotStatus = deriveAnnotationStatus({
      hasReviewed: m.observations.some(isHumanReviewed),
      hasAi:       m.observations.some(isAiLabel),
    })

    // Top label: a human verdict, then the consensus row, then the first row (#170)
    const { labelObs: topObs, isEmpty } = photoVerdict(m.observations)
    const name = topObs && !isEmpty && (topObs.scientific_name || topObs.vernacular_name || topObs.observation_type)
      ? observationLabel(topObs) : null
    const conf = topObs?.classification_probability ?? null
    const hint = [topObs?.scientific_name, fileName].filter(Boolean).join(' · ')
    const isOpen = selectedMediaId === m.id
    const inatSt  = inatStates.get(m.id)
    const sel = selectedIds.has(m.id)
    const stuck = !retryRequested.has(m.deployment_id) && isThumbnailStuck(m, loadedAt, busy)

    return (
      <div key={m.id} {...cardProps(m, null)} style={cardStyle(sel, isOpen)}>
        {/* Thumbnail area */}
        <div style={{ ...IMAGE_AREA, backgroundColor: imgUrl ? undefined : 'rgba(0,0,0,0.04)' }}>
          {imgUrl && !failedThumbs.has(m.id) ? (
            <img
              src={imgUrl}
              alt={fileName || 'photo'}
              loading="lazy"
              decoding="async"
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              onError={() => setFailedThumbs(s => new Set(s).add(m.id))}
            />
          ) : (
            // Stuck: the thumbnail should exist and nothing is making it (one Retry above the
            // grid). Otherwise still generating.
            <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              {stuck ? <ImageOff size={18} aria-hidden /> : <Hourglass size={18} aria-hidden />}
              {!compact && <span>{stuck ? 'No thumbnail' : 'Processing…'}</span>}
            </span>
          )}

          {/* Status badge, top-left. No badge until a label exists. */}
          {annotStatus !== 'pending' && (
            <span style={{ position: 'absolute', top: 4, left: 4 }}>
              <StatusBadge status={annotStatus} size="sm" />
            </span>
          )}

          {/* Phase 3: iNaturalist dove badge — top-right overlay */}
          {inatSt && (
            <span style={{ position: 'absolute', top: 4, right: 4 }}>
              <INatBadge state={inatSt.state} uri={inatSt.uri} />
            </span>
          )}

          {/* Selection circle, bottom-left: its own button */}
          {renderSelectCircle(m, sel)}
        </div>

        {renderLabelBar(name, isEmpty, conf, hint, m.timestamp, m.deployment_id)}
      </div>
    )
  }

  // ── Crop view: one card per observation ────────────────────────────────────
  // Renders an individual detection's crop (observations.crop_url). Selection and
  // open are keyed by media id (actions are media-level), so the existing grid
  // machinery is reused. Falls back to the full frame when a crop is missing.
  const renderCropCard = (m: MediaRecord, obs: ObservationRecord, key: string) => {
    const cropUrl = obs.crop_url || null
    const imgUrl = cropUrl || displayImageUrl(m, 'thumb')
    const noCrop = !cropUrl
    const sel = selectedIds.has(m.id)
    const isOpen = selectedMediaId === m.id
    // Once a person gave the photo its verdict, each crop shows that verdict, not the AI's.
    const verdict = photoVerdict(m.observations)
    const ruled = !!verdict.labelObs && isHumanReviewed(verdict.labelObs) ? verdict : null
    const shown = ruled ? ruled.labelObs : obs
    const status = ruled ? 'reviewed' : deriveAnnotationStatus({ hasReviewed: isHumanReviewed(obs), hasAi: isAiLabel(obs) })
    const isEmpty = ruled ? ruled.isEmpty : !obs.scientific_name && obs.observation_type === 'blank'
    const name = shown && !isEmpty && (shown.scientific_name || shown.vernacular_name || shown.observation_type)
      ? observationLabel(shown) : null
    const conf = ruled ? null : obs.classification_probability ?? null
    const aiSaid = ruled && isAiLabel(obs) && obs.scientific_name ? `AI: ${obs.scientific_name}` : null
    const hint = [shown?.scientific_name, aiSaid, m.file_name].filter(Boolean).join(' · ')

    return (
      <div key={key} {...cardProps(m, obs.id)} style={cardStyle(sel, isOpen)}>
        <div style={{ ...IMAGE_AREA, backgroundColor: imgUrl ? undefined : 'rgba(0,0,0,0.04)' }}>
          {imgUrl ? (
            <img
              src={imgUrl}
              alt={name || 'crop'}
              loading="lazy"
              decoding="async"
              // Crops are tight cut-outs — 'contain' shows the whole animal;
              // a full-frame fallback fills the card with 'cover'.
              style={{ width: '100%', height: '100%', objectFit: noCrop ? 'cover' : 'contain' }}
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <ImageOff size={24} aria-hidden style={{ color: 'var(--text-muted)' }} />
          )}
          {status !== 'pending' && (
            <span style={{ position: 'absolute', top: 4, left: 4 }}>
              <StatusBadge status={status} size="sm" />
            </span>
          )}
          {noCrop && imgUrl && !compact && (
            <span style={{ position: 'absolute', bottom: 4, right: 4, fontSize: '0.6rem', padding: '0.05rem 0.3rem', borderRadius: '3px', background: 'rgba(0,0,0,0.55)', color: '#fff' }}>
              full frame
            </span>
          )}
          {renderSelectCircle(m, sel)}
        </div>
        {renderLabelBar(name, isEmpty, conf, hint, null, m.deployment_id)}
      </div>
    )
  }

  // Expand a media into one card per label: AI crops + human labels (full-frame
  // when they have no crop), at most MAX_LABEL_CARDS. Un-labelled / cropless AI frames are omitted.
  const cropCardsFor = (m: MediaRecord) => labelCards(m).map(o => renderCropCard(m, o, o.id))
  const renderCards = (items: MediaRecord[]) =>
    imageView === 'crop' ? items.flatMap(m => cropCardsFor(m)) : items.map(m => renderThumbCard(m))

  const toggleGroupSelection = (items: MediaRecord[], allSelected: boolean) => {
    const next = new Set(selectedIds)
    for (const m of items) {
      if (allSelected) next.delete(m.id)
      else next.add(m.id)
    }
    setSelectedIds(next)
  }

  const pagerText = win.total === null
    ? null
    : win.hasPrev || win.hasNext
      ? `${win.from.toLocaleString()}–${win.to.toLocaleString()} of ${win.total.toLocaleString()}`
      : `${win.total.toLocaleString()} photo${win.total !== 1 ? 's' : ''}`
  const writableOnPage = visibleSelectable.length > 0
  return (
    <div>
      {/* ── "Being processed" banner for in-view deployments ───────── */}
      <ProcessingBanner deployments={deployments} />

      {/* ── Ribbon command bar (sticky so filters, the pager and actions stay reachable
          while scrolling the photo grid) ──────────────────────────── */}
      <Ribbon
        sticky
        status={
          <div style={{ display: 'flex', alignItems: 'center' }}>
            {/* ── Pager: one count, the filtered total (02 #5, #24) ── */}
            {!similarToId && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', whiteSpace: 'nowrap' }}>
                {(win.hasPrev || win.hasNext) && (
                  <button type="button" onClick={() => goToPage(page - 1)} disabled={!win.hasPrev} aria-label="Previous page" style={pagerButton(!win.hasPrev)}>
                    <ChevronLeft size={16} aria-hidden />
                  </button>
                )}
                {pagerText && <span style={{ color: 'var(--text-muted)' }}>{pagerText}</span>}
                {(win.hasPrev || win.hasNext) && (
                  <button type="button" onClick={() => goToPage(page + 1)} disabled={!win.hasNext} aria-label="Next page" style={pagerButton(!win.hasNext)}>
                    <ChevronRight size={16} aria-hidden />
                  </button>
                )}
                {stats.uploading > 0 && <span style={{ color: 'var(--primary)' }}>· {stats.uploading} uploading</span>}
              </span>
            )}

            {/* ── Display controls (what to show) ── */}
            <span style={RIBBON_SECTION}>
              <span style={{ display: 'flex', flexShrink: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }} title="Photo shows the full frame; Labels shows each cropped, labelled detection">
                {(['photo', 'crop'] as const).map(v => (
                  <button key={v} onClick={() => setImageView(v)}
                    style={{ fontSize: '0.72rem', padding: '0.2rem 0.55rem', border: 'none', cursor: 'pointer',
                      background: imageView === v ? 'var(--primary)' : 'transparent', color: imageView === v ? '#fff' : 'var(--text-color)', fontWeight: imageView === v ? 600 : 400 }}>
                    {v === 'photo' ? 'Photo' : 'Labels'}
                  </button>
                ))}
              </span>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', color: 'var(--text-muted)', flexShrink: 0 }} title="Thumbnail size">
                <Search size={13} aria-hidden />
                <input type="range" min={80} max={280} step={10} value={thumbScale} onChange={e => setThumbScale(+e.target.value)}
                  aria-label="Thumbnail size" style={{ width: 90, accentColor: 'var(--primary)', cursor: 'pointer' }} />
              </label>
            </span>

            {/* ── Selection: only for photos the user can label (decision 1) ── */}
            {writableOnPage && (
              <span style={RIBBON_SECTION}>
                <button type="button" onClick={selectAllOnPage} title="Select every photo on this page"
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--primary)', padding: '0.2rem 0.4rem', whiteSpace: 'nowrap' }}>
                  All on page
                </button>
              </span>
            )}
          </div>
        }
        subBar={(selectedIds.size > 0 || advancedActive) ? (
          <>
            {selectedIds.size > 0 && (
              <MediaBulkActions
                selectedCount={selectedIds.size}
                onClearSelection={clearSelection}
                onAction={handleBulkAction}
                inatConnected={!!inat.connected}
                inatEnabled={inat.enabled}
                canTrain={roles.managesAnyOrg}
              />
            )}
            {advancedActive && (
              <button onClick={clearAdvanced}
                style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem', color: 'var(--primary)', padding: '0.2rem 0.5rem' }}>
                <X size={13} aria-hidden /> Clear advanced filters
              </button>
            )}
          </>
        ) : undefined}
        tabs={[
          {
            id: 'filter', label: 'Filter',
            groups: [
              { id: 'deployment', title: 'Deployment', content: (
                <MultiSelect values={filterDeployments} onChange={setFilterDeployments} allLabel="All deployments" noun="deployment"
                  searchable options={deploymentOptions} />
              ) },
              { id: 'species', title: 'Species', content: (
                <MultiSelect values={filters.species} onChange={species => setFilters(f => ({ ...f, species }))} allLabel="All species" noun="species" nounPlural="species"
                  searchable onOpen={() => setWantSpeciesOptions(true)}
                  loading={filterOptions.isFetching}
                  // A chosen species stays listed while the options load, or after a deep link.
                  options={[...new Set([...filters.species, ...(filterOptions.data?.species ?? []).map(o => o.value)])].map(s => ({ value: s, label: s }))} />
              ) },
              {
                id: 'refine', title: 'More filters',
                content: (
                  <button
                    onClick={() => setDraft(filters)}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                      padding: '0.35rem 0.7rem', fontSize: '0.75rem', cursor: 'pointer',
                      borderRadius: 'var(--radius)', whiteSpace: 'nowrap',
                      border: `1px solid ${advancedActive ? 'var(--primary)' : 'var(--border)'}`,
                      backgroundColor: advancedActive ? 'rgba(76,175,80,0.1)' : 'transparent',
                      color: advancedActive ? 'var(--primary)' : 'var(--text-color)',
                    }}
                  >
                    <SlidersHorizontal size={13} aria-hidden /> Advanced filters{advancedCount ? ` (${advancedCount})` : ''}
                  </button>
                ),
              },
              ...(reviewOrderOn ? [{ id: 'order', title: 'Order', content: (
                <FilterSelect value={orderBy} onChange={v => setOrderBy(v === 'review' ? 'review' : '')} placeholder="Newest first"
                  options={[{ value: 'review', label: 'Needs review first' }]} />
              ) }] : []),
            ],
          },
          {
            id: 'group', label: 'Group',
            groups: [
              { id: 'group-mode', title: 'Group by', content: (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <FilterSelect value={groupBy === 'none' ? '' : groupBy} onChange={v => setGroupChoice((v || 'none') as GroupBy)} placeholder="None"
                    options={groupOptions.map(g => ({ value: g, label: GROUP_LABELS[g] }))} />
                  {groupedMedia && groupedMedia.length > 1 && (
                    collapsed.size < groupedMedia.length ? (
                      <button type="button" style={SMALL_BTN} onClick={() => setCollapsedAt({ groupBy, labels: new Set(groupedMedia.map(([label]) => label)) })}>
                        <ChevronsDownUp size={13} aria-hidden /> Collapse all
                      </button>
                    ) : (
                      <button type="button" style={SMALL_BTN} onClick={() => setCollapsedAt({ groupBy, labels: EMPTY_IDS })}>
                        <ChevronsUpDown size={13} aria-hidden /> Expand all
                      </button>
                    )
                  )}
                  {!singleDeployment && brainOn && (
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                      {activeDeploymentIds.length > DEPLOYMENT_GROUP_LIMIT
                        ? `Pick one deployment to group by cluster, ${DEPLOYMENT_GROUP_LIMIT} or fewer to group by deployment`
                        : 'Pick one deployment to group by cluster'}
                    </span>
                  )}
                </div>
              ) },
              ...(groupBy === 'cluster' ? [{ id: 'cluster-threshold', title: 'Similarity', content: (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Loose</span>
                  <input type="range" min={0} max={1} step={0.05} value={clusterThreshold}
                    onChange={e => setClusterThreshold(+e.target.value)} aria-label="Cluster similarity"
                    style={{ width: 80, accentColor: 'var(--primary)', cursor: 'pointer' }} />
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Tight</span>
                  <span style={{ fontSize: '0.7rem', fontWeight: 600, minWidth: '2rem' }}>{(clusterThreshold * 100).toFixed(0)}%</span>
                  {clustersQ.isLoading && <Hourglass size={13} aria-label="Loading clusters" style={{ color: 'var(--text-muted)' }} />}
                </div>
              ) }] : []),
              // Deeper clustering tools (embed/reassign/tiered review + the UMAP
              // map) live on their own per-deployment pages — linked here so they
              // stay reachable now that the standalone Explore page is retired.
              ...(groupBy === 'cluster' && singleDeployment && clusterRun.data?.embedding_run_id ? [{ id: 'cluster-advanced', title: 'Advanced', content: (
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <Link to={`/clusters/${singleDeployment}`} title="Embed, reassign and tiered cluster review" style={SMALL_BTN}>Review clusters</Link>
                  <Link to={`/umap/${singleDeployment}`} title="2-D embedding map of this deployment" style={SMALL_BTN}>UMAP</Link>
                </div>
              ) }] : []),
            ],
          },
        ]}
      />

      {notice && (
        <Banner tone={notice.tone} onClose={() => setNotice(null)}>{notice.text}</Banner>
      )}
      {loading && <p style={{ color: 'var(--text-muted)' }}>Loading media…</p>}
      {error && <Banner tone="error">{error}</Banner>}

      {/* ── Not-hosted notice ──────────────────────────────────────── */}
      {/* Keyed on the loaded rows, not the loading flag: the 4 s refetch during an upload sets
          loading on every poll, which blinked the old banner on and off. Optimistic cards are
          not counted (see stats), so an upload in progress no longer triggers it either. */}
      {stats.noImage > 0 && (
        <Banner tone="warning" icon={ImageOff}>
          {stats.noImage === stats.total ? 'These photos are' : `${stats.noImage} of these photos are`} not
          hosted online, so only their records can be shown. Upload the originals from the Upload page to
          attach them, or set a hosted URL in the media detail panel.
        </Banner>
      )}

      {/* ── Stuck thumbnails: one notice, one Retry for their deployments (02 #22) ── */}
      {stuckThumbs.length > 0 && !similarToId && (
        <Banner
          tone="warning"
          icon={ImageOff}
          action={
            <button type="button" className="btn btn-outline" style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
              title="Make the missing thumbnails for these deployments"
              onClick={() => stuckDeploymentIds.forEach(id => retryThumbnails(id))}>
              Retry
            </button>
          }
        >
          {stuckThumbs.length} photo{stuckThumbs.length !== 1 ? 's' : ''} on this page {stuckThumbs.length !== 1 ? 'have' : 'has'} no thumbnail.
        </Banner>
      )}

      {/* ── Thumbnail grid (full width) — selecting a photo opens the modal ── */}
      <div>
        <div>
          {similarToId ? (
            <>
              {/* Find-similar mode: ranked neighbours of the anchor image */}
              <Banner
                tone="info"
                icon={Search}
                action={
                  <button type="button" onClick={() => setSimilarToId(null)}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.8rem' }}>
                    <X size={13} aria-hidden /> Clear
                  </button>
                }
              >
                <strong>{Math.max(0, similarRecords.length - 1)}</strong> photos similar to the selected one, most similar first (the selected one leads)
              </Banner>
              {similarLoading && similarRecords.length === 0 && (
                <p style={{ color: 'var(--text-muted)', padding: '1rem 0' }}>Searching for similar photos…</p>
              )}
              {similarQ.isError && (
                <Banner tone="error">No embedding for this photo yet. Run clustering for this deployment first.</Banner>
              )}
              <div style={gridStyle}>
                {similarRecords.map(m => renderThumbCard(m))}
              </div>
            </>
          ) : (
          <>
          {!loading && filtered.length === 0 && (
            <p style={{ color: 'var(--text-muted)', padding: '1rem 0' }}>No photos match these filters.</p>
          )}
          {!loading && filtered.length > 0 && imageView === 'crop' && cropCardCount === 0 && (
            <p style={{ color: 'var(--text-muted)', padding: '1rem 0' }}>
              No labels on this page yet. Switch to <strong>Photo</strong> to see and label the full frames.
            </p>
          )}
          {/* Grouped view — collapsible sections */}
          {groupedMedia ? groupedMedia.map(([groupLabel, groupItems]) => {
            const clusterInfo = clusterByLabel.get(groupLabel)
            const groupSelectable = groupItems.filter(isSelectable)
            const allSelected = groupSelectable.length > 0 && groupSelectable.every(m => selectedIds.has(m.id))
            const labelAll = !!(clusterInfo && !clusterInfo.confirmed && singleDeployment && canWriteDeployment(singleDeployment))
            return (
            <MediaGroup
              key={groupLabel}
              label={groupLabel}
              count={groupItems.length}
              open={!collapsed.has(groupLabel)}
              onToggle={() => {
                const next = new Set(collapsed)
                if (!next.delete(groupLabel)) next.add(groupLabel)
                setCollapsedAt({ groupBy, labels: next })
              }}
              action={(groupSelectable.length > 0 || labelAll) ? (
                <>
                  {groupSelectable.length > 0 && (
                    <button type="button" style={SMALL_BTN} onClick={() => toggleGroupSelection(groupSelectable, allSelected)}
                      title={allSelected ? 'Deselect the photos in this group' : 'Select the photos in this group'}>
                      {allSelected ? 'Deselect' : 'Select'}
                    </button>
                  )}
                  {labelAll && clusterInfo && singleDeployment && (
                    <ClusterLabelAll clusterId={clusterInfo.id} deploymentId={singleDeployment} onDone={onClusterConfirmed} />
                  )}
                </>
              ) : undefined}
            >
              <div style={gridStyle}>
                {renderCards(groupItems)}
              </div>
            </MediaGroup>
            )
          }) : (
          /* Flat (ungrouped) grid */
          <div style={gridStyle}>
            {renderCards(filtered)}
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
            mediaList={visible}
            onSelect={setSelectedMediaId}
            onClose={() => setSelectedMediaId(null)}
            onUpdated={handleMediaUpdated}
            onNext={advanceToNext}
            onPrev={advanceToPrev}
            focusObsId={focusObsId}
            previewStuck={!retryRequested.has(selectedMedia.deployment_id) && isThumbnailStuck(selectedMedia, loadedAt, busy)}
            onRetryPreview={() => retryThumbnails(selectedMedia.deployment_id)}
          />
        )}
      </div>

      {/* ── Advanced filters (WS5-T3): a draft, applied in one query ── */}
      <Modal
        open={draft !== null}
        onClose={() => setDraft(null)}
        title="Advanced filters"
        size="sm"
        footer={
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button
              onClick={() => setDraft(d => (d ? clearedAdvanced(d) : d))}
              style={{
                padding: '0.5rem 1rem', fontSize: '0.875rem',
                border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
              }}
            >
              Clear all
            </button>
            <button
              onClick={() => { if (draft) setFilters(draft); setDraft(null) }}
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
        {draft && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

          {/* Observation attribute filters (moved out of the main ribbon) */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Annotation</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={draft.status} onChange={v => editDraft({ status: v as GridFilters['status'] })} placeholder="Any status"
                options={[
                  { value: 'ai',       label: 'AI identified' },
                  { value: 'reviewed', label: 'Reviewed'      },
                  { value: 'pending',  label: 'Not labelled'  },
                ]} />
              <FilterSelect value={draft.annotationType} onChange={v => editDraft({ annotationType: v as GridFilters['annotationType'] })} placeholder="Any type"
                options={[
                  { value: 'bbox',  label: 'Bounding box' },
                  { value: 'whole', label: 'Whole image'  },
                ]} />
              <FilterSelect value={draft.annotator} onChange={v => editDraft({ annotator: v })}
                placeholder={filterOptions.isFetching ? 'Loading annotators…' : 'Any annotator'}
                options={[...new Set([draft.annotator, ...(filterOptions.data?.annotators ?? []).map(o => o.value)].filter(Boolean))].map(a => ({ value: a, label: a }))} />
              <FilterSelect value={draft.model} onChange={v => editDraft({ model: v })}
                placeholder={filterOptions.isFetching ? 'Loading AI models…' : 'Any AI model'}
                options={[...new Set([draft.model, ...(filterOptions.data?.models ?? []).map(o => o.value)].filter(Boolean))].map(v => ({ value: v, label: modelOptionLabel(v) }))} />
            </div>
          </section>

          {/* Animal attributes */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Animal</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={draft.sex} onChange={v => editDraft({ sex: v })} placeholder="Any sex"
                options={SEX_VALUES.map(capitalised)} />
              <FilterSelect value={draft.lifeStage} onChange={v => editDraft({ lifeStage: v })} placeholder="Any life stage"
                options={LIFE_STAGE_VALUES.map(capitalised)} />
            </div>
          </section>

          {/* Date range */}
          <section>
            <div style={{ fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }}>Date range</div>
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                <span style={{ color: 'var(--text-muted)' }}>From</span>
                <input
                  type="date"
                  value={draft.dateFrom}
                  onChange={e => editDraft({ dateFrom: e.target.value })}
                  style={{
                    padding: '0.375rem 0.5rem', borderRadius: 'var(--radius)',
                    border: '1px solid var(--border)', backgroundColor: 'var(--surface)',
                    color: 'var(--text-color)', fontSize: '0.8125rem',
                  }}
                />
              </label>
              <label style={{ fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                <span style={{ color: 'var(--text-muted)' }}>To</span>
                <input
                  type="date"
                  value={draft.dateTo}
                  onChange={e => editDraft({ dateTo: e.target.value })}
                  style={{
                    padding: '0.375rem 0.5rem', borderRadius: 'var(--radius)',
                    border: '1px solid var(--border)', backgroundColor: 'var(--surface)',
                    color: 'var(--text-color)', fontSize: '0.8125rem',
                  }}
                />
              </label>
            </div>
          </section>

        </div>
        )}
      </Modal>

      {/* ── Bulk action modals ──────────────────────────────────── */}
      {showDeleteModal && (
        <DeleteConfirmModal
          mediaIds={Array.from(selectedIds)}
          fileNames={visible.flatMap(m => (selectedIds.has(m.id) ? [m.file_name || m.file_path.split('/').pop() || m.id] : []))}
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
          seeds={labelledSpecies(media.flatMap(m => m.observations))}
        />
      )}
      {showTrainModal && (() => {
        // The selection as the trainer will see it (optimistic upload placeholders are not rows yet).
        const rows = visible.filter(m => selectedIds.has(m.id) && !m._pending)
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

