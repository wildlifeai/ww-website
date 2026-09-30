import { describe, expect, it } from 'vitest'
import { backgroundCount, defaultBackgroundLabel, defaultLabelFor, sanitizeLabel, summarizeSelection, type MediaLike } from './trainingDataset'

const obs = (id: string, name: string | null, extra: Partial<MediaLike['observations'][number]> = {}) => ({
  id, observation_type: name === null ? 'blank' : 'animal', scientific_name: name, ai_origin: 'cloud', ...extra,
})
const media = (id: string, observations: MediaLike['observations']): MediaLike => ({ id, deployment_id: 'd', observations })

describe('summarizeSelection', () => {
  it('prefers human observations and ignores edge ones', () => {
    const s = summarizeSelection([
      media('m1', [obs('a', 'Mus musculus'), obs('b', 'Rattus rattus', { reviewer_id: 'u' }), obs('c', 'Rattus rattus', { ai_origin: 'edge' })]),
    ])
    expect(s.classes).toEqual([{ scientific_name: 'Rattus rattus', vernacular_name: null, taxon_id: null, count: 1, human: 1 }])
  })

  it('counts blanks once per image and reports unlabelled / edge-only', () => {
    const s = summarizeSelection([
      media('m1', [obs('a', null), obs('b', null)]),
      media('m2', []),
      media('m3', [obs('c', 'X', { ai_origin: 'edge' })]),
    ])
    expect(s.blanks).toBe(1)
    expect(s.unlabelled).toBe(1)
    expect(s.edgeOnly).toBe(1)
  })

  it('merges case variants and keeps the first vernacular name', () => {
    const s = summarizeSelection([
      media('m1', [obs('a', 'rattus rattus', { vernacular_name: 'Ship rat' })]),
      media('m2', [obs('b', 'Rattus rattus')]),
    ])
    expect(s.classes[0].count).toBe(2)
    expect(s.classes[0].vernacular_name).toBe('Ship rat')
    expect(backgroundCount(s, new Set(['rattus rattus']))).toBe(0)
    expect(backgroundCount(s, new Set())).toBe(2)
  })
})

describe('labels', () => {
  it('sanitises and derives defaults', () => {
    expect(sanitizeLabel('  Ship  Rat! ')).toBe('ship rat')
    expect(defaultLabelFor({ scientific_name: 'Rattus rattus', vernacular_name: 'Ship rat' })).toBe('ship rat')
    expect(defaultLabelFor({ scientific_name: 'Rattus rattus', vernacular_name: null })).toBe('rattus rattus')
  })

  it('picks a background label that sorts first', () => {
    expect(defaultBackgroundLabel(['rat'])).toBe('not rat')
    expect(defaultBackgroundLabel(['gecko'])).toBe('background')
    expect(defaultBackgroundLabel(['rat', 'stoat'])).toBe('other')
    expect(defaultBackgroundLabel(['a'])).toBe('_background')
  })
})
