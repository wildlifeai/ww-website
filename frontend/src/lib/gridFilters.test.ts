import { describe, expect, it } from 'vitest'
import {
  applyFilterOps, gridFilterPlan, gridQueryKey, hasAdvancedFilters, hasAnyFilter, NO_FILTERS, type FilterableQuery,
} from './gridFilters'

const f = (over: Partial<typeof NO_FILTERS>) => ({ ...NO_FILTERS, ...over })

describe('gridFilterPlan', () => {
  it('is empty with no filters', () => {
    expect(gridFilterPlan(NO_FILTERS)).toEqual({ embeds: [], ops: [] })
  })

  it('keeps photos with a row of a chosen species, through its own inner embed', () => {
    expect(gridFilterPlan(f({ species: ['Rattus rattus', 'Mus musculus'] }))).toEqual({
      embeds: ['f_species:observations!inner()'],
      ops: [{ op: 'in', column: 'f_species.scientific_name', values: ['Rattus rattus', 'Mus musculus'] }],
    })
  })

  it('gives each filter its own alias, so two rows may meet two filters', () => {
    const plan = gridFilterPlan(f({ species: ['Rattus rattus'], annotator: 'a@b.nz', model: 'speciesnet-4' }))
    expect(plan.embeds).toEqual(['f_species:observations!inner()', 'f_annotator:observations!inner()', 'f_model:observations!inner()'])
    expect(plan.ops).toContainEqual({ op: 'eq', column: 'f_annotator.classification_method', value: 'human' })
    expect(plan.ops).toContainEqual({ op: 'eq', column: 'f_annotator.classified_by', value: 'a@b.nz' })
    expect(plan.ops).toContainEqual({ op: 'eq', column: 'f_model.source_model_version', value: 'speciesnet-4' })
  })

  it('finds reviewed photos by review_status, as isHumanReviewed does', () => {
    const plan = gridFilterPlan(f({ status: 'reviewed' }))
    expect(plan.embeds).toEqual(['f_reviewed:observations!inner()'])
    expect(plan.ops[0]).toMatchObject({ op: 'in', column: 'f_reviewed.review_status' })
    expect((plan.ops[0] as { values: string[] }).values.sort()).toEqual(['consensus_approved', 'expert_reviewed', 'human_reviewed'])
  })

  it('finds AI-only photos: an AI row and no reviewed row', () => {
    const plan = gridFilterPlan(f({ status: 'ai' }))
    expect(plan.embeds).toEqual(['f_ai:observations!inner()', 'f_reviewed:observations()'])
    expect(plan.ops).toEqual([
      { op: 'or', filters: 'source_type.eq.ai,classification_method.eq.machine,review_status.eq.ai_reviewed', referencedTable: 'f_ai' },
      expect.objectContaining({ op: 'in', column: 'f_reviewed.review_status' }),
      { op: 'isNull', column: 'f_reviewed' },
    ])
  })

  it('finds photos not labelled yet with an anti-join on any label', () => {
    const plan = gridFilterPlan(f({ status: 'pending' }))
    expect(plan.embeds).toEqual(['f_labelled:observations()'])
    expect(plan.ops[0]).toMatchObject({ op: 'or', referencedTable: 'f_labelled' })
    expect(plan.ops[1]).toEqual({ op: 'isNull', column: 'f_labelled' })
  })

  it('splits boxed photos from whole-image ones, keeping photos with no rows as whole', () => {
    expect(gridFilterPlan(f({ annotationType: 'bbox' })).ops).toEqual([{ op: 'notNull', column: 'f_box.bbox_x' }])
    const whole = gridFilterPlan(f({ annotationType: 'whole' }))
    expect(whole.embeds).toEqual(['f_box:observations()'])
    expect(whole.ops).toEqual([{ op: 'notNull', column: 'f_box.bbox_x' }, { op: 'isNull', column: 'f_box' }])
  })

  it('keeps photos with no capture time in a date range', () => {
    expect(gridFilterPlan(f({ dateFrom: '2026-01-01' })).ops)
      .toEqual([{ op: 'or', filters: 'timestamp.is.null,timestamp.gte.2026-01-01' }])
    expect(gridFilterPlan(f({ dateTo: '2026-02-01' })).ops)
      .toEqual([{ op: 'or', filters: 'timestamp.is.null,timestamp.lte.2026-02-01T23:59:59' }])
    expect(gridFilterPlan(f({ dateFrom: '2026-01-01', dateTo: '2026-02-01' })).ops)
      .toEqual([{ op: 'or', filters: 'timestamp.is.null,and(timestamp.gte.2026-01-01,timestamp.lte.2026-02-01T23:59:59)' }])
  })

  it('ignores a date that is not a plain day, so nothing odd reaches the or= filter', () => {
    expect(gridFilterPlan(f({ dateFrom: '2026-01-01,x' })).ops).toEqual([])
  })
})

describe('applyFilterOps', () => {
  it('turns each op into the matching supabase-js call', () => {
    const calls: unknown[][] = []
    type Recorder = FilterableQuery<Recorder>
    const q: Recorder = {
      in: (...a) => { calls.push(['in', ...a]); return q },
      eq: (...a) => { calls.push(['eq', ...a]); return q },
      not: (...a) => { calls.push(['not', ...a]); return q },
      is: (...a) => { calls.push(['is', ...a]); return q },
      or: (...a) => { calls.push(['or', ...a]); return q },
    }
    applyFilterOps(q, [
      { op: 'in', column: 'a.x', values: ['1'] },
      { op: 'eq', column: 'a.y', value: '2' },
      { op: 'notNull', column: 'a.z' },
      { op: 'isNull', column: 'a' },
      { op: 'or', filters: 'p.eq.1', referencedTable: 'a' },
      { op: 'or', filters: 'p.eq.2' },
    ])
    expect(calls).toEqual([
      ['in', 'a.x', ['1']], ['eq', 'a.y', '2'], ['not', 'a.z', 'is', null], ['is', 'a', null],
      ['or', 'p.eq.1', { referencedTable: 'a' }], ['or', 'p.eq.2'],
    ])
  })
})

describe('filter summaries', () => {
  it('tells advanced filters from the species filter', () => {
    expect(hasAdvancedFilters(f({ species: ['x'] }))).toBe(false)
    expect(hasAnyFilter(f({ species: ['x'] }))).toBe(true)
    expect(hasAdvancedFilters(f({ dateTo: '2026-01-01' }))).toBe(true)
    expect(hasAnyFilter(NO_FILTERS)).toBe(false)
  })

  it('keys a query by its deployments and filters, ignoring species order', () => {
    expect(gridQueryKey(['d1'], f({ species: ['b', 'a'] }))).toBe(gridQueryKey(['d1'], f({ species: ['a', 'b'] })))
    expect(gridQueryKey(['d1'], NO_FILTERS)).not.toBe(gridQueryKey(['d2'], NO_FILTERS))
    expect(gridQueryKey(['d1'], NO_FILTERS)).not.toBe(gridQueryKey(['d1'], f({ status: 'ai' })))
  })
})
