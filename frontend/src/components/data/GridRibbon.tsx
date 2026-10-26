// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// GridRibbon: the review grid's sticky command bar. The menu bar holds the pager (one count, the
// filtered total, 02 #5, #24), the Photo / Labels toggle, the thumbnail size and "All on page";
// the sub-bar the selection's actions; the Filter and Group tabs the query and the grouping.
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, ChevronsDownUp, ChevronsUpDown, Hourglass, Search, SlidersHorizontal, X } from 'lucide-react'
import { FilterSelect } from '../ui/ControlBar'
import { MultiSelect } from '../ui/MultiSelect'
import { Ribbon } from '../ui/Ribbon'
import type { FilterOption } from '../../lib/deploymentFilterOptions'
import { DEPLOYMENT_GROUP_LIMIT, GROUP_LABELS, type GroupBy } from '../../lib/gridGrouping'
import { pagerText, type PageWindow } from '../../lib/gridPage'
import { RIBBON_SECTION, SMALL_BTN, pagerButton } from './gridStyles'

export type ImageView = 'photo' | 'crop'

interface Props {
  pager: { win: PageWindow; page: number; onPage: (p: number) => void; uploading: number } | null
  view: { image: ImageView; onImage: (v: ImageView) => void; thumbScale: number; onThumbScale: (n: number) => void }
  /** "All on page", for someone who can label photos on it. */
  onSelectAll: (() => void) | null
  /** The selection's bar, shown in the sub-bar. */
  bulkBar: ReactNode
  filter: {
    deployments: string[]
    onDeployments: (ids: string[]) => void
    deploymentOptions: FilterOption[]
    species: string[]
    onSpecies: (species: string[]) => void
    onSpeciesOpen: () => void
    speciesOptions: string[]
    optionsLoading: boolean
    advancedCount: number
    onAdvanced: () => void
    onClearAdvanced: () => void
    /** "Needs review first", when the active-learning flag is on. */
    order: { value: '' | 'review'; onChange: (v: '' | 'review') => void } | null
  }
  group: {
    groupBy: GroupBy
    options: GroupBy[]
    onGroupBy: (g: GroupBy) => void
    /** The page's group labels, when grouped. */
    labels: string[] | null
    collapsed: ReadonlySet<string>
    onCollapsed: (labels: ReadonlySet<string>) => void
    deploymentCount: number
    singleDeployment: string | null
    brainOn: boolean
    clusterThreshold: number
    onClusterThreshold: (n: number) => void
    clustersLoading: boolean
    embeddingRunId: string | null
  }
}

const NONE: ReadonlySet<string> = new Set()

export function GridRibbon({ pager, view, onSelectAll, bulkBar, filter, group }: Props) {
  const win = pager?.win
  const paged = !!win && (win.hasPrev || win.hasNext)
  const text = win ? pagerText(win) : null
  const advancedActive = filter.advancedCount > 0
  const labels = group.labels
  return (
    <Ribbon
      sticky
      status={
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {pager && win && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', whiteSpace: 'nowrap' }}>
              {paged && (
                <button type="button" onClick={() => pager.onPage(pager.page - 1)} disabled={!win.hasPrev} aria-label="Previous page" style={pagerButton(!win.hasPrev)}>
                  <ChevronLeft size={16} aria-hidden />
                </button>
              )}
              {text && <span style={{ color: 'var(--text-muted)' }}>{text}</span>}
              {paged && (
                <button type="button" onClick={() => pager.onPage(pager.page + 1)} disabled={!win.hasNext} aria-label="Next page" style={pagerButton(!win.hasNext)}>
                  <ChevronRight size={16} aria-hidden />
                </button>
              )}
              {pager.uploading > 0 && <span style={{ color: 'var(--primary)' }}>· {pager.uploading} uploading</span>}
            </span>
          )}

          {/* Display controls (what to show) */}
          <span style={RIBBON_SECTION}>
            <span style={{ display: 'flex', flexShrink: 0, border: '1px solid var(--border)', borderRadius: 'var(--radius)', overflow: 'hidden' }} title="Photo shows the full frame; Labels shows each cropped, labelled detection">
              {(['photo', 'crop'] as const).map(v => (
                <button key={v} onClick={() => view.onImage(v)}
                  style={{ fontSize: '0.72rem', padding: '0.2rem 0.55rem', border: 'none', cursor: 'pointer',
                    background: view.image === v ? 'var(--primary)' : 'transparent', color: view.image === v ? '#fff' : 'var(--text-color)', fontWeight: view.image === v ? 600 : 400 }}>
                  {v === 'photo' ? 'Photo' : 'Labels'}
                </button>
              ))}
            </span>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', color: 'var(--text-muted)', flexShrink: 0 }} title="Thumbnail size">
              <Search size={13} aria-hidden />
              <input type="range" min={80} max={280} step={10} value={view.thumbScale} onChange={e => view.onThumbScale(+e.target.value)}
                aria-label="Thumbnail size" style={{ width: 90, accentColor: 'var(--primary)', cursor: 'pointer' }} />
            </label>
          </span>

          {/* Selection: only for photos the user can label (decision 1) */}
          {onSelectAll && (
            <span style={RIBBON_SECTION}>
              <button type="button" onClick={onSelectAll} title="Select every photo on this page"
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--primary)', padding: '0.2rem 0.4rem', whiteSpace: 'nowrap' }}>
                All on page
              </button>
            </span>
          )}
        </div>
      }
      subBar={(bulkBar || advancedActive) ? (
        <>
          {bulkBar}
          {advancedActive && (
            <button onClick={filter.onClearAdvanced}
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
              <MultiSelect values={filter.deployments} onChange={filter.onDeployments} allLabel="All deployments" noun="deployment"
                searchable options={filter.deploymentOptions} />
            ) },
            { id: 'species', title: 'Species', content: (
              <MultiSelect values={filter.species} onChange={filter.onSpecies} allLabel="All species" noun="species" nounPlural="species"
                searchable onOpen={filter.onSpeciesOpen}
                loading={filter.optionsLoading}
                // A chosen species stays listed while the options load, or after a deep link.
                options={[...new Set([...filter.species, ...filter.speciesOptions])].map(s => ({ value: s, label: s }))} />
            ) },
            {
              id: 'refine', title: 'More filters',
              content: (
                <button
                  onClick={filter.onAdvanced}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                    padding: '0.35rem 0.7rem', fontSize: '0.75rem', cursor: 'pointer',
                    borderRadius: 'var(--radius)', whiteSpace: 'nowrap',
                    border: `1px solid ${advancedActive ? 'var(--primary)' : 'var(--border)'}`,
                    backgroundColor: advancedActive ? 'rgba(76,175,80,0.1)' : 'transparent',
                    color: advancedActive ? 'var(--primary)' : 'var(--text-color)',
                  }}
                >
                  <SlidersHorizontal size={13} aria-hidden /> Advanced filters{filter.advancedCount ? ` (${filter.advancedCount})` : ''}
                </button>
              ),
            },
            ...(filter.order ? [{ id: 'order', title: 'Order', content: (
              <FilterSelect value={filter.order.value} onChange={v => filter.order?.onChange(v === 'review' ? 'review' : '')} placeholder="Newest first"
                options={[{ value: 'review', label: 'Needs review first' }]} />
            ) }] : []),
          ],
        },
        {
          id: 'group', label: 'Group',
          groups: [
            { id: 'group-mode', title: 'Group by', content: (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <FilterSelect value={group.groupBy === 'none' ? '' : group.groupBy} onChange={v => group.onGroupBy((v || 'none') as GroupBy)} placeholder="None"
                  options={group.options.map(g => ({ value: g, label: GROUP_LABELS[g] }))} />
                {labels && labels.length > 1 && (
                  group.collapsed.size < labels.length ? (
                    <button type="button" style={SMALL_BTN} onClick={() => group.onCollapsed(new Set(labels))}>
                      <ChevronsDownUp size={13} aria-hidden /> Collapse all
                    </button>
                  ) : (
                    <button type="button" style={SMALL_BTN} onClick={() => group.onCollapsed(NONE)}>
                      <ChevronsUpDown size={13} aria-hidden /> Expand all
                    </button>
                  )
                )}
                {!group.singleDeployment && group.brainOn && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {group.deploymentCount > DEPLOYMENT_GROUP_LIMIT
                      ? `Pick one deployment to group by cluster, ${DEPLOYMENT_GROUP_LIMIT} or fewer to group by deployment`
                      : 'Pick one deployment to group by cluster'}
                  </span>
                )}
              </div>
            ) },
            ...(group.groupBy === 'cluster' ? [{ id: 'cluster-threshold', title: 'Similarity', content: (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Loose</span>
                <input type="range" min={0} max={1} step={0.05} value={group.clusterThreshold}
                  onChange={e => group.onClusterThreshold(+e.target.value)} aria-label="Cluster similarity"
                  style={{ width: 80, accentColor: 'var(--primary)', cursor: 'pointer' }} />
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Tight</span>
                <span style={{ fontSize: '0.7rem', fontWeight: 600, minWidth: '2rem' }}>{(group.clusterThreshold * 100).toFixed(0)}%</span>
                {group.clustersLoading && <Hourglass size={13} aria-label="Loading clusters" style={{ color: 'var(--text-muted)' }} />}
              </div>
            ) }] : []),
            // Deeper clustering tools (embed, reassign, tiered review and the UMAP map) live on
            // their own per-deployment pages, linked here.
            ...(group.groupBy === 'cluster' && group.singleDeployment && group.embeddingRunId ? [{ id: 'cluster-advanced', title: 'Advanced', content: (
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <Link to={`/clusters/${group.singleDeployment}`} title="Embed, reassign and tiered cluster review" style={SMALL_BTN}>Review clusters</Link>
                <Link to={`/umap/${group.singleDeployment}`} title="2-D embedding map of this deployment" style={SMALL_BTN}>UMAP</Link>
              </div>
            ) }] : []),
          ],
        },
      ]}
    />
  )
}
