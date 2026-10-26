// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// AdvancedFiltersModal: the review grid's Advanced filters (WS5-T3). It edits a draft, applied in
// one query on Apply (lib/gridFilters).
import { FilterSelect } from '../ui/ControlBar'
import { Modal } from '../ui/Modal'
import { modelOptionLabel } from '../../hooks/useGridFilterOptions'
import type { GridFacets } from '../../lib/gridFacets'
import { clearedAdvanced, type GridFilters } from '../../lib/gridFilters'
import { DAY_START_HOUR, NIGHT_START_HOUR } from '../../lib/time'

// The fixed values the observations table allows (ww-backend CHECK constraints).
const SEX_VALUES = ['female', 'male', 'unknown']
const LIFE_STAGE_VALUES = ['adult', 'subadult', 'juvenile', 'hatchling', 'unknown']
const capitalised = (s: string) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) })
const clock = (h: number) => `${String(h).padStart(2, '0')}:00`
const TIME_OF_DAY_OPTIONS = [
  { value: 'day', label: `Day (${clock(DAY_START_HOUR)} to ${clock(NIGHT_START_HOUR)})` },
  { value: 'night', label: `Night (${clock(NIGHT_START_HOUR)} to ${clock(DAY_START_HOUR)})` },
]

const SECTION_TITLE: React.CSSProperties = { fontWeight: 600, fontSize: '0.875rem', marginBottom: '0.5rem' }
const DATE_INPUT: React.CSSProperties = {
  padding: '0.375rem 0.5rem', borderRadius: 'var(--radius)',
  border: '1px solid var(--border)', backgroundColor: 'var(--surface)',
  color: 'var(--text-color)', fontSize: '0.8125rem',
}
const DATE_LABEL: React.CSSProperties = { fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }

/** A select's options from facet values, the chosen value kept while the options load. */
const facetOptions = (chosen: string, values: readonly { value: string }[] | undefined, label = (v: string) => v) =>
  [...new Set([chosen, ...(values ?? []).map(o => o.value)].filter(Boolean))].map(v => ({ value: v, label: label(v) }))

interface Props {
  /** The draft being edited; null when the dialog is closed. */
  draft: GridFilters | null
  onDraft: (next: GridFilters | null) => void
  onApply: (filters: GridFilters) => void
  /** The annotator and AI model lists (lib/gridFacets). */
  facets: GridFacets | undefined
  facetsLoading: boolean
}

export function AdvancedFiltersModal({ draft, onDraft, onApply, facets, facetsLoading }: Props) {
  const edit = (patch: Partial<GridFilters>) => { if (draft) onDraft({ ...draft, ...patch }) }
  return (
    <Modal
      open={draft !== null}
      onClose={() => onDraft(null)}
      title="Advanced filters"
      size="sm"
      footer={
        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
          <button
            onClick={() => { if (draft) onDraft(clearedAdvanced(draft)) }}
            style={{
              padding: '0.5rem 1rem', fontSize: '0.875rem',
              border: '1px solid var(--border)', borderRadius: 'var(--radius)',
              background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
            }}
          >
            Clear all
          </button>
          <button
            onClick={() => { if (draft) onApply(draft); onDraft(null) }}
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
          <section>
            <div style={SECTION_TITLE}>Annotation</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={draft.status} onChange={v => edit({ status: v as GridFilters['status'] })} placeholder="Any status"
                options={[
                  { value: 'ai',       label: 'AI identified' },
                  { value: 'reviewed', label: 'Reviewed'      },
                  { value: 'pending',  label: 'Not labelled'  },
                ]} />
              <FilterSelect value={draft.annotationType} onChange={v => edit({ annotationType: v as GridFilters['annotationType'] })} placeholder="Any type"
                options={[
                  { value: 'bbox',  label: 'Bounding box' },
                  { value: 'whole', label: 'Whole image'  },
                ]} />
              <FilterSelect value={draft.annotator} onChange={v => edit({ annotator: v })}
                placeholder={facetsLoading ? 'Loading annotators…' : 'Any annotator'}
                options={facetOptions(draft.annotator, facets?.annotators)} />
              <FilterSelect value={draft.model} onChange={v => edit({ model: v })}
                placeholder={facetsLoading ? 'Loading AI models…' : 'Any AI model'}
                options={facetOptions(draft.model, facets?.models, modelOptionLabel)} />
            </div>
          </section>

          <section>
            <div style={SECTION_TITLE}>Animal</div>
            <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
              <FilterSelect value={draft.sex} onChange={v => edit({ sex: v })} placeholder="Any sex"
                options={SEX_VALUES.map(capitalised)} />
              <FilterSelect value={draft.lifeStage} onChange={v => edit({ lifeStage: v })} placeholder="Any life stage"
                options={LIFE_STAGE_VALUES.map(capitalised)} />
            </div>
          </section>

          <section>
            <div style={SECTION_TITLE}>Date range</div>
            <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={DATE_LABEL}>
                <span style={{ color: 'var(--text-muted)' }}>From</span>
                <input type="date" value={draft.dateFrom} onChange={e => edit({ dateFrom: e.target.value })} style={DATE_INPUT} />
              </label>
              <label style={DATE_LABEL}>
                <span style={{ color: 'var(--text-muted)' }}>To</span>
                <input type="date" value={draft.dateTo} onChange={e => edit({ dateTo: e.target.value })} style={DATE_INPUT} />
              </label>
            </div>
          </section>

          {/* Time of day: the capture hour in the deployment's zone */}
          <section>
            <div style={SECTION_TITLE}>Time of day</div>
            <FilterSelect value={draft.timeOfDay} onChange={v => edit({ timeOfDay: v as GridFilters['timeOfDay'] })} placeholder="Any time"
              options={TIME_OF_DAY_OPTIONS} />
          </section>
        </div>
      )}
    </Modal>
  )
}
