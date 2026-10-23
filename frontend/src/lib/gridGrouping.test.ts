import { describe, expect, it } from 'vitest'
import {
  availableGroupings, DEPLOYMENT_GROUP_LIMIT, groupingAvailable, groupInOrder, observationGroupKey, type GroupableObservation,
} from './gridGrouping'

const ai = (scientific_name: string, extra: Partial<GroupableObservation> = {}): GroupableObservation =>
  ({ source_type: 'ai', review_status: 'ai_reviewed', classification_method: 'machine', observation_type: 'animal', scientific_name, ...extra })
const human = (scientific_name: string | null, extra: Partial<GroupableObservation> = {}): GroupableObservation =>
  ({ source_type: 'human', review_status: 'human_reviewed', classification_method: 'human', observation_type: scientific_name ? 'animal' : 'blank', scientific_name, ...extra })

describe('groupingAvailable', () => {
  it('offers clusters for exactly one deployment', () => {
    expect(groupingAvailable('cluster', 1)).toBe(true)
    expect(groupingAvailable('cluster', 2)).toBe(false)
  })

  it('offers deployment groups only for a few deployments', () => {
    expect(groupingAvailable('deployment', 1)).toBe(false)
    expect(groupingAvailable('deployment', DEPLOYMENT_GROUP_LIMIT)).toBe(true)
    expect(groupingAvailable('deployment', DEPLOYMENT_GROUP_LIMIT + 1)).toBe(false)
    expect(availableGroupings(100)).not.toContain('deployment')
    expect(availableGroupings(100)).toContain('species')
  })
})

describe('observationGroupKey', () => {
  it('groups by the verdict, so a human correction wins over the AI row listed first', () => {
    expect(observationGroupKey('species', [ai('Rattus rattus'), human('Mus musculus')])).toBe('Mus musculus')
    expect(observationGroupKey('species', [ai('Rattus rattus'), human(null)])).toBe('(Empty)')
    expect(observationGroupKey('species', [])).toBe('(No species)')
  })

  it('reads sex and life stage from the verdict row too', () => {
    const obs = [ai('Rattus rattus', { sex: 'male' }), human('Mus musculus', { sex: 'female', life_stage: 'adult' })]
    expect(observationGroupKey('sex', obs)).toBe('female')
    expect(observationGroupKey('life_stage', obs)).toBe('adult')
    expect(observationGroupKey('life_stage', [ai('Rattus rattus')])).toBe('(Unknown)')
  })

  it('keeps the photo-level groupings', () => {
    expect(observationGroupKey('annotation_type', [ai('x', { bbox_x: 0.1 })])).toBe('Bounding box')
    expect(observationGroupKey('annotation_type', [])).toBe('Whole image')
    expect(observationGroupKey('model', [ai('x', { source_model_version: 'speciesnet-4' })])).toBe('speciesnet-4')
    expect(observationGroupKey('annotator', [human('x', { classified_by: 'a@b.nz' })])).toBe('a@b.nz')
  })
})

describe('groupInOrder', () => {
  it('puts the largest group first', () => {
    expect(groupInOrder(['a', 'b', 'b'], s => s)).toEqual([['b', ['b', 'b']], ['a', ['a']]])
  })
})
