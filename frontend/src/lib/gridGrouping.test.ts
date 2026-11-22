import { describe, expect, it } from 'vitest'
import {
  availableGroupings, DEPLOYMENT_GROUP_LIMIT, groupingAvailable, groupInOrder, observationGroupKey, photoGroupKey, splitsIntoGroups,
  type GroupableObservation, type GroupNames,
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
    expect(availableGroupings(100, [], NAMES)).not.toContain('deployment')
    expect(availableGroupings(100, [], NAMES)).toContain('species')
  })
})

const NAMES: GroupNames = {
  deployments: new Map([['d1', 'Ridge'], ['d2', 'Stream'], ['d3', 'Hut']]),
  projects: new Map([['d1', 'Weta'], ['d2', 'Weta'], ['d3', 'Kiwi']]),
}

describe('availableGroupings on a page of photos', () => {
  it('offers only the groupings that split the page into two or more groups', () => {
    const photos = [
      { deployment_id: 'd1', observations: [human('Deinacrida', { sex: 'female' })] },
      { deployment_id: 'd2', observations: [human('Rattus rattus')] },
    ]
    const offered = availableGroupings(2, photos, NAMES)
    expect(offered).toEqual(expect.arrayContaining(['species', 'sex', 'deployment']))
    // Neither photo has a life stage, a box, or an AI row, and both projects are Weta.
    expect(offered).not.toContain('life_stage')
    expect(offered).not.toContain('annotation_type')
    expect(offered).not.toContain('model')
    expect(offered).not.toContain('project')
  })

  it('offers projects when the page holds photos from two of them', () => {
    const photos = [
      { deployment_id: 'd1', observations: [] },
      { deployment_id: 'd3', observations: [] },
    ]
    expect(availableGroupings(2, photos, NAMES)).toContain('project')
    expect(photoGroupKey('project', photos[1], NAMES)).toBe('Kiwi')
  })

  it('still offers clusters for one deployment, whose groups load only once chosen', () => {
    expect(availableGroupings(1, [{ deployment_id: 'd1', observations: [] }], NAMES)).toEqual(['cluster'])
  })
})

describe('splitsIntoGroups', () => {
  it('needs two different keys', () => {
    expect(splitsIntoGroups([], (x: string) => x)).toBe(false)
    expect(splitsIntoGroups(['a', 'a'], x => x)).toBe(false)
    expect(splitsIntoGroups(['a', 'a', 'b'], x => x)).toBe(true)
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
