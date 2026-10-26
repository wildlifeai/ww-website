// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GridBody: the review grid's cards. The "Find similar" list, ranked neighbours of one photo;
// otherwise the page, flat or in collapsible groups, where a group offers Select for its photos
// and an unconfirmed cluster "Label all".
import { Search, X } from 'lucide-react'
import type { CSSProperties } from 'react'
import type { MediaRecord } from '../../lib/gridMedia'
import { Banner } from '../ui/Banner'
import { ClusterLabelAll } from './ClusterLabelAll'
import { GridCards, type CardEnv } from './GridCards'
import { MediaGroup } from './MediaGroup'
import { SMALL_BTN } from './gridStyles'

interface Props {
  similar: { records: MediaRecord[]; loading: boolean; failed: boolean; onClear: () => void } | null
  loading: boolean
  /** The page's cards, uploading placeholders last. */
  cards: MediaRecord[]
  labelsView: boolean
  /** Cards the Labels view shows on this page. */
  labelCardCount: number
  groups: [string, MediaRecord[]][] | null
  collapsed: ReadonlySet<string>
  onToggleGroup: (label: string) => void
  onSelectGroup: (ids: string[], allSelected: boolean) => void
  clusterByLabel: ReadonlyMap<string, { id: string; confirmed: boolean }>
  /** "Label all" on an unconfirmed cluster: its deployment and project, when the user may label it. */
  labelAll: { deploymentId: string; projectId?: string; onDone: (created: number, name: string) => void } | null
  gridStyle: CSSProperties
  env: CardEnv
}

const MUTED: CSSProperties = { color: 'var(--text-muted)', padding: '1rem 0' }

export function GridBody({
  similar, loading, cards, labelsView, labelCardCount, groups, collapsed, onToggleGroup, onSelectGroup, clusterByLabel, labelAll,
  gridStyle, env,
}: Props) {
  if (similar) {
    return (
      <>
        <Banner
          tone="info"
          icon={Search}
          action={
            <button type="button" onClick={similar.onClear}
              style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--primary)', fontSize: '0.8rem' }}>
              <X size={13} aria-hidden /> Clear
            </button>
          }
        >
          <strong>{Math.max(0, similar.records.length - 1)}</strong> photos similar to the selected one, most similar first (the selected one leads)
        </Banner>
        {similar.loading && similar.records.length === 0 && <p style={MUTED}>Searching for similar photos…</p>}
        {similar.failed && (
          <Banner tone="error">No embedding for this photo yet. Run clustering for this deployment first.</Banner>
        )}
        <div style={gridStyle}>
          <GridCards items={similar.records} labelsView={false} env={env} />
        </div>
      </>
    )
  }

  return (
    <>
      {!loading && cards.length === 0 && <p style={MUTED}>No photos match these filters.</p>}
      {!loading && cards.length > 0 && labelsView && labelCardCount === 0 && (
        <p style={MUTED}>
          No labels on this page yet. Switch to <strong>Photo</strong> to see and label the full frames.
        </p>
      )}
      {groups ? groups.map(([groupLabel, groupItems]) => {
        const clusterInfo = clusterByLabel.get(groupLabel)
        const groupSelectable = groupItems.filter(env.isSelectable)
        const allSelected = groupSelectable.length > 0 && groupSelectable.every(m => env.selectedIds.has(m.id))
        const canLabelAll = !!(clusterInfo && !clusterInfo.confirmed && labelAll)
        return (
          <MediaGroup
            key={groupLabel}
            label={groupLabel}
            count={groupItems.length}
            open={!collapsed.has(groupLabel)}
            onToggle={() => onToggleGroup(groupLabel)}
            action={(groupSelectable.length > 0 || canLabelAll) ? (
              <>
                {groupSelectable.length > 0 && (
                  <button type="button" style={SMALL_BTN} onClick={() => onSelectGroup(groupSelectable.map(m => m.id), allSelected)}
                    title={allSelected ? 'Deselect the photos in this group' : 'Select the photos in this group'}>
                    {allSelected ? 'Deselect' : 'Select'}
                  </button>
                )}
                {canLabelAll && clusterInfo && labelAll && (
                  <ClusterLabelAll clusterId={clusterInfo.id} deploymentId={labelAll.deploymentId} projectId={labelAll.projectId} onDone={labelAll.onDone} />
                )}
              </>
            ) : undefined}
          >
            <div style={gridStyle}>
              <GridCards items={groupItems} labelsView={labelsView} env={env} />
            </div>
          </MediaGroup>
        )
      }) : (
        <div style={gridStyle}>
          <GridCards items={cards} labelsView={labelsView} env={env} />
        </div>
      )}
    </>
  )
}
