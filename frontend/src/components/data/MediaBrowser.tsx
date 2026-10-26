// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// MediaBrowser: the review grid on Annotations. It holds the grid's choices (deployments,
// filters, order, page, grouping, view) and wires the parts: the page's photos
// (hooks/useGridMedia), thumbnails (useGridThumbnails), selection (useGridSelection), the
// Actions menu (useGridBulkActions), iNaturalist (useGridInat), the ribbon, the cards and the viewer.
import { useEffect, useMemo, useState } from 'react'
import { useAuth } from '../../hooks/useAuth'
import { useDeploymentFilterOptions } from '../../hooks/useDeploymentFilterOptions'
import { useFeatureEnabled } from '../../hooks/useFeatureEnabled'
import { useGridBulkActions } from '../../hooks/useGridBulkActions'
import { useGridFilterOptions } from '../../hooks/useGridFilterOptions'
import { useGridFilterState } from '../../hooks/useGridFilterState'
import { useGridGroups } from '../../hooks/useGridGroups'
import { useGridInat } from '../../hooks/useGridInat'
import { useGridMedia } from '../../hooks/useGridMedia'
import { useGridNotice } from '../../hooks/useGridNotice'
import { useGridSelection } from '../../hooks/useGridSelection'
import { useGridThumbnails } from '../../hooks/useGridThumbnails'
import { useGridViewer } from '../../hooks/useGridViewer'
import { useProjectRoles } from '../../hooks/useProjectRoles'
import { useSimilarMedia } from '../../hooks/useSimilarMedia'
import { useQueryClient } from '@tanstack/react-query'
import { useUploadStore } from '../../contexts/UploadContext'
import type { CardIntent } from '../../lib/cardSelection'
import { advancedFilterCount, clearedAdvanced, gridFilterPlan, gridQueryKey, gridScopePlan, hasAnyFilter, type GridSort } from '../../lib/gridFilters'
import { labelCards, pageStats, pendingMediaRecord, selectableInOrder, type MediaRecord } from '../../lib/gridMedia'
import type { GroupBy } from '../../lib/gridGrouping'
import { pageWindow } from '../../lib/gridPage'
import { survivingPending } from '../../lib/pendingCards'
import { AdvancedFiltersModal } from './AdvancedFiltersModal'
import { GridBody } from './GridBody'
import { GridBulkDialogs } from './GridBulkDialogs'
import type { CardEnv } from './GridCards'
import { GridNotices } from './GridNotices'
import { GridRibbon, type ImageView } from './GridRibbon'
import { MediaBulkActions } from './MediaBulkActions'
import { MediaDetail } from './MediaDetail'
import { ProcessingBanner } from './ProcessingBanner'
import { gridStyle } from './gridStyles'

export type { MediaAssetRecord, MediaRecord, ObservationRecord } from '../../lib/gridMedia'

interface Props {
  deployments: { id: string; location_name: string | null; project_id: string; timezone?: string | null; deployment_start?: string | null }[]
  /** WS5-T6: Pre-select a deployment when navigating from the upload dock. */
  initialDeploymentId?: string
  /** Pre-apply a species filter (e.g. deep-linked from an Insights chart). */
  initialSpecies?: string
  /** Open this photo in the viewer (e.g. linked from the UMAP explorer). */
  initialMediaId?: string
}

export function MediaBrowser({ deployments, initialDeploymentId, initialSpecies, initialMediaId }: Props) {
  const { user } = useAuth()
  const roles = useProjectRoles()
  const qc = useQueryClient()
  // deployment_id → IANA timezone, so capture times render in the photo's local zone.
  const tzByDeployment = useMemo(() => new Map(deployments.map(d => [d.id, d.timezone ?? null])), [deployments])
  const projectByDeployment = useMemo(() => new Map(deployments.map(d => [d.id, d.project_id])), [deployments])
  const deploymentNames = useMemo(() => new Map(deployments.map(d => [d.id, d.location_name || d.id.slice(0, 8)])), [deployments])
  const deploymentOptions = useDeploymentFilterOptions(deployments)
  const { isActive: uploadActive, pendingUploads, pendingSince } = useUploadStore()
  const { notice, flash, flashError, showCardNotice, dismiss } = useGridNotice()
  const f = useGridFilterState(initialSpecies, initialDeploymentId)
  const view = useGridViewPrefs()
  const [groupChoice, setGroupChoice] = useState<GroupBy>('none')
  const [clusterThreshold, setClusterThreshold] = useState(0.0)

  // The deployments the query reads, keyed by value so a new array with the same ids (the
  // timezone fill, a parent re-render) does not refetch or reset the page.
  const deploymentKey = (f.deployments.length ? f.deployments : deployments.map(d => d.id)).join(',')
  const activeDeploymentIds = useMemo(() => (deploymentKey ? deploymentKey.split(',') : []), [deploymentKey])
  // The query itself names the selected projects unless deployments were picked (02 #8).
  const projectKey = [...new Set(deployments.map(d => d.project_id))].toSorted().join(',')
  const scope = useMemo(() => gridScopePlan(f.deployments, projectKey ? projectKey.split(',') : []), [f.deployments, projectKey])
  const queryKey = gridQueryKey(activeDeploymentIds, f.filters)
  const plan = useMemo(() => gridFilterPlan(f.filters), [f.filters])
  const singleDeployment = activeDeploymentIds.length === 1 ? activeDeploymentIds[0] : null
  // Clusters, UMAP and the active-learning order exist only where their backend flag is on
  // (decision 6); the flags are global, so any deployment in view can be asked.
  const brainOn = useFeatureEnabled('brain', activeDeploymentIds[0] ?? null)
  const reviewOrderOn = useFeatureEnabled('activeLearning', activeDeploymentIds[0] ?? null)
  const filterOptions = useGridFilterOptions(activeDeploymentIds, f.wantSpeciesOptions || f.draft !== null)
  // "Needs review first": the retired Review Queue's order (decision 6), a sort of the media
  // query, so every filter and the pager apply (lib/gridFilters, ww-backend #337).
  const sort: GridSort = f.orderBy === 'review' && reviewOrderOn ? 'review' : 'newest'

  // Page and selection belong to one query: a new filter or order goes back to page 1, and a
  // new page (or the similar list) starts with nothing selected.
  const listKey = `${queryKey}|${sort}`
  const [pageAt, setPageAt] = useState({ key: '', page: 0 })
  const page = pageAt.key === listKey ? pageAt.page : 0
  const goToPage = (p: number) => {
    setPageAt({ key: listKey, page: Math.max(0, p) })
    window.scrollTo({ top: 0 })
  }

  const grid = useGridMedia({ user, deploymentIds: activeDeploymentIds, queryKey, scope, plan, sort, page, uploadActive })
  const similar = useSimilarMedia(grid.setError)
  const thumbs = useGridThumbnails({
    media: grid.media, failedThumbs: grid.failedThumbs, loadedAt: grid.loadedAt, shownDeploymentIds: activeDeploymentIds,
    reload: grid.reload, refreshQuietly: grid.refreshQuietly, onError: flashError,
  })

  // ── Roles (decision 1): only people who can write to a photo's project select it ──
  const canWriteDeployment = (deploymentId: string) => {
    const project = projectByDeployment.get(deploymentId)
    return !roles.loading && !!project && roles.canWrite(project)
  }
  const isSelectable = (m: MediaRecord) => !m._pending && canWriteDeployment(m.deployment_id)

  // Optimistic cards for files still uploading (no DB row yet), shown instantly via the local
  // preview so the grid isn't empty right after an upload redirect. Scoped to the deployment(s)
  // in view, on the first unfiltered page, and retired one for one as real rows land (see
  // lib/pendingCards for why a name match is not enough). They come last, after the real photos.
  const showPending = page === 0 && !hasAnyFilter(f.filters)
  const cards = useMemo<MediaRecord[]>(() => {
    if (pendingUploads.length === 0 || !showPending) return grid.media
    const pending = survivingPending(pendingUploads, grid.media, pendingSince, new Set(activeDeploymentIds)).map(pendingMediaRecord)
    return pending.length ? [...grid.media, ...pending] : grid.media
  }, [pendingUploads, pendingSince, grid.media, activeDeploymentIds, showPending])
  // The cards on screen: the page, or the similarity results.
  const visible = similar.similarToId ? similar.records : cards
  const visibleSelectable = visible.filter(isSelectable)

  const viewer = useGridViewer(visible, initialMediaId, user?.id)
  const selection = useGridSelection(`${listKey}|${page}|${similar.similarToId ?? ''}`, visibleSelectable.map(m => m.id), !!viewer.selectedMediaId)
  const { selectedIds, clearSelection } = selection
  const inat = useGridInat({ media: grid.media, flash, showCardNotice, onPublished: clearSelection })
  const bulk = useGridBulkActions({
    user, selectedIds, visible, media: grid.media, isSelectable, clearSelection, reload: grid.reload,
    setMedia: grid.setMedia, setSimilar: similar.setRecords, setTotalCount: grid.setTotalCount,
    onFindSimilar: similar.setSimilarToId, onPublish: inat.publish,
  })
  const groups = useGridGroups({ cards, choice: groupChoice, deploymentIds: activeDeploymentIds, deploymentNames, brainOn, clusterThreshold })

  // After a cluster is bulk-confirmed: refresh the cluster assignments (relabels
  // the group) and reload media so the new observations show on the thumbnails.
  const onClusterConfirmed = (created: number, name: string) => {
    qc.invalidateQueries({ queryKey: ['brain', 'clusters-multi'] })
    grid.reload(true)
    flash('success', `Labelled cluster as ${name}${created ? ` · ${created} observation${created !== 1 ? 's' : ''}` : ''}`)
  }

  if (deployments.length === 0) {
    return <p style={{ color: 'var(--text-muted)', padding: '2rem 0' }}>Select a project or add deployments to browse media.</p>
  }

  const stats = pageStats(cards)
  const win = pageWindow(page, stats.total, grid.totalCount)
  const labelsView = view.image === 'crop'
  const compact = view.thumbScale < 120

  // ── Card gestures (#283, rules in lib/cardSelection.ts) ───────────────────
  const onCardIntent = (id: string, intent: CardIntent, focusObs: string | null) => {
    if (intent === 'open') { viewer.open(id, focusObs); return }
    // The grid's selectable photos in rendered order, for a Shift-click range.
    const order = similar.similarToId
      ? selectableInOrder(visible, isSelectable, false)
      : selectableInOrder(groups.groups ? groups.groups.flatMap(([, items]) => items) : cards, isSelectable, labelsView)
    selection.select(id, intent, order)
  }
  const env: CardEnv = {
    compact, roomy: view.thumbScale >= 200, openId: viewer.selectedMediaId, selectedIds, isSelectable, isStuck: thumbs.isStuck,
    failedThumbs: grid.failedThumbs, onThumbError: grid.markThumbFailed, inatStates: inat.inatStates,
    timezoneOf: id => tzByDeployment.get(id), onIntent: onCardIntent,
  }
  const handleMediaUpdated = (updated: MediaRecord) => {
    const swap = (list: MediaRecord[]) => list.map(m => m.id === updated.id ? updated : m)
    grid.setMedia(swap)
    similar.setRecords(swap)
    viewer.updateLinked(updated)
  }
  const selectedMedia = viewer.selectedMedia

  return (
    <div>
      {/* "Being processed" banner for in-view deployments */}
      <ProcessingBanner deployments={deployments} />

      <GridRibbon
        pager={similar.similarToId ? null : { win, page, onPage: goToPage, uploading: stats.uploading }}
        view={{ image: view.image, onImage: view.setImage, thumbScale: view.thumbScale, onThumbScale: view.setThumbScale }}
        onSelectAll={visibleSelectable.length > 0 ? selection.selectAllOnPage : null}
        bulkBar={selectedIds.size > 0 && (
          <MediaBulkActions selectedCount={selectedIds.size} onClearSelection={clearSelection} onAction={bulk.onAction}
            inatConnected={!!inat.inat.connected} inatEnabled={inat.inat.enabled} canTrain={roles.managesAnyOrg} />
        )}
        filter={{
          deployments: f.deployments, onDeployments: f.setDeployments, deploymentOptions,
          species: f.filters.species, onSpecies: species => f.setFilters(x => ({ ...x, species })),
          onSpeciesOpen: () => f.setWantSpeciesOptions(true),
          speciesOptions: (filterOptions.data?.species ?? []).map(o => o.value), optionsLoading: filterOptions.isFetching,
          advancedCount: advancedFilterCount(f.filters), onAdvanced: () => f.setDraft(f.filters),
          onClearAdvanced: () => f.setFilters(clearedAdvanced),
          order: reviewOrderOn ? { value: f.orderBy, onChange: f.setOrderBy } : null,
        }}
        group={{
          groupBy: groups.groupBy, options: groups.options, onGroupBy: setGroupChoice,
          labels: groups.groups ? groups.groups.map(([label]) => label) : null,
          collapsed: groups.collapsed, onCollapsed: groups.setCollapsed,
          deploymentCount: activeDeploymentIds.length, singleDeployment, brainOn,
          clusterThreshold, onClusterThreshold: setClusterThreshold,
          clustersLoading: groups.clustersLoading, embeddingRunId: groups.embeddingRunId,
        }}
      />

      <GridNotices notice={notice} onDismiss={dismiss} loading={grid.loading} error={grid.error} stats={stats}
        stuckCount={similar.similarToId ? 0 : thumbs.stuckCount} onRetry={() => thumbs.stuckDeploymentIds.forEach(id => thumbs.retryThumbnails(id))} />

      {/* Thumbnail grid (full width); opening a photo shows the viewer */}
      <div>
        <div>
          <GridBody
            similar={similar.similarToId
              ? { records: similar.records, loading: similar.loading, failed: similar.failed, onClear: () => similar.setSimilarToId(null) }
              : null}
            loading={grid.loading} cards={cards} labelsView={labelsView}
            labelCardCount={labelsView ? cards.reduce((n, m) => n + labelCards(m).length, 0) : 0}
            groups={groups.groups} collapsed={groups.collapsed} onToggleGroup={groups.toggleCollapsed} onSelectGroup={selection.toggleGroup}
            clusterByLabel={groups.clusterByLabel}
            labelAll={singleDeployment && canWriteDeployment(singleDeployment)
              ? { deploymentId: singleDeployment, projectId: projectByDeployment.get(singleDeployment), onDone: onClusterConfirmed }
              : null}
            gridStyle={gridStyle(view.thumbScale, compact)} env={env}
          />
        </div>

        {/* Full-screen labeling modal */}
        {selectedMedia && (
          <MediaDetail
            media={selectedMedia}
            timezone={tzByDeployment.get(selectedMedia.deployment_id)}
            mediaList={visible}
            onSelect={viewer.setSelectedMediaId}
            onClose={() => viewer.setSelectedMediaId(null)}
            onUpdated={handleMediaUpdated}
            onNext={viewer.next}
            onPrev={viewer.prev}
            focusObsId={viewer.focusObsId}
            previewStuck={thumbs.isStuck(selectedMedia)}
            onRetryPreview={() => thumbs.retryThumbnails(selectedMedia.deployment_id)}
          />
        )}
      </div>

      <AdvancedFiltersModal draft={f.draft} onDraft={f.setDraft} onApply={f.setFilters}
        facets={filterOptions.data} facetsLoading={filterOptions.isFetching} />

      <GridBulkDialogs bulk={bulk} selectedIds={selectedIds} visible={visible} media={grid.media} deployments={deployments} />
    </div>
  )
}

/** Thumbnail size and Photo / Labels, remembered in this browser; grouping is not (02 #12). */
function useGridViewPrefs() {
  const [thumbScale, setThumbScale] = useState(() => {
    const saved = localStorage.getItem('ww:thumbScale')
    return saved ? Number(saved) : 140
  })
  // Photo (full frame) vs crop (the detected animal cut-out). Crops make it fast
  // to scan a species for misclassifications; photos reveal animals missing a box.
  const [image, setImage] = useState<ImageView>(() => (localStorage.getItem('ww:imageView') === 'crop' ? 'crop' : 'photo'))
  useEffect(() => { localStorage.setItem('ww:thumbScale', String(thumbScale)) }, [thumbScale])
  useEffect(() => { localStorage.setItem('ww:imageView', image) }, [image])
  return { thumbScale, setThumbScale, image, setImage }
}
