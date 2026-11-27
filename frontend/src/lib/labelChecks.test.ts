import { describe, expect, it } from 'vitest'
import {
  confusedPairs, inPair, minScore, orderChecks, reasonText, stillLabelled, strongestByMedia, toLabelCheck, type LabelCheck,
} from './labelChecks'

const check = (id: string, over: Partial<LabelCheck> = {}): LabelCheck => ({
  id, observation_id: `o-${id}`, project_id: 'p1', kind: 'look_alike', score: 0.8, other_scientific_name: 'Rattus norvegicus',
  reference: 'confirmed', label_scientific_name: 'Rattus rattus', media_id: `m-${id}`, confirmed: true, ...over,
})

describe('toLabelCheck', () => {
  it('takes the photo and whether a person stands behind the label from the flagged observation', () => {
    const row = { ...check('a'), observations: { media_id: 'm1', review_status: 'human_reviewed', source_type: 'ai', classification_method: 'machine' } }
    expect(toLabelCheck(row)).toMatchObject({ id: 'a', media_id: 'm1', confirmed: true })
    expect(toLabelCheck({ ...row, observations: { media_id: 'm1', review_status: 'ai_reviewed', source_type: 'ai' } })?.confirmed).toBe(false)
    expect(toLabelCheck({ ...row, observations: { media_id: null } })).toBeNull()
  })
})

describe('minScore', () => {
  it('runs from 1 at no sensitivity to 0.5, every flag, at full', () => {
    expect(minScore(0)).toBe(1)
    expect(minScore(80)).toBeCloseTo(0.6)
    expect(minScore(100)).toBe(0.5)
    expect(minScore(140)).toBe(0.5)
  })
})

describe('reasonText', () => {
  it('words each kind of flag', () => {
    expect(reasonText(check('a'))).toBe('Looks like Rattus norvegicus, labelled Rattus rattus')
    expect(reasonText(check('a', { kind: 'odd_one_out', other_scientific_name: null }))).toBe('Unlike other Rattus rattus')
    expect(reasonText(check('a', { kind: 'more_specific', label_scientific_name: 'Rattus', other_scientific_name: 'Rattus rattus' })))
      .toBe('Could be Rattus rattus')
  })
})

describe('stillLabelled', () => {
  it('holds while the photo shows the flagged label, whatever its case or spacing', () => {
    expect(stillLabelled(check('a'), 'rattus  Rattus')).toBe(true)
    expect(stillLabelled(check('a'), 'Mus musculus')).toBe(false)
    expect(stillLabelled(check('a'), null)).toBe(false)
  })
})

describe('orderChecks', () => {
  it('puts confirmed labels first, more specific last, AI references lower, then the strongest', () => {
    const list = [
      check('ai-label', { confirmed: false, score: 0.99 }),
      check('weak', { score: 0.6 }),
      check('strong', { score: 0.9 }),
      check('ai-ref', { reference: 'ai', score: 0.95 }),
      check('specific', { kind: 'more_specific', score: 1 }),
    ]
    expect(orderChecks(list).map(c => c.id)).toEqual(['strong', 'weak', 'ai-ref', 'specific', 'ai-label'])
  })
})

describe('confusedPairs and inPair', () => {
  const list = [
    check('a'),
    check('b', { label_scientific_name: 'Rattus norvegicus', other_scientific_name: 'Rattus rattus' }),
    check('c', { label_scientific_name: 'Mus musculus', other_scientific_name: 'Rattus rattus' }),
    check('d', { kind: 'odd_one_out', other_scientific_name: null }),
  ]

  it('counts look-alike pairs either way round, most first', () => {
    expect(confusedPairs(list)).toEqual([
      { species: ['Rattus norvegicus', 'Rattus rattus'], count: 2 },
      { species: ['Mus musculus', 'Rattus rattus'], count: 1 },
    ])
  })

  it('keeps both species of a pair', () => {
    expect(list.filter(c => inPair(c, ['Rattus norvegicus', 'Rattus rattus'])).map(c => c.id)).toEqual(['a', 'b'])
  })
})

describe('strongestByMedia', () => {
  it('keeps each photo\'s strongest flag', () => {
    const byMedia = strongestByMedia([check('a', { media_id: 'm1', score: 0.6 }), check('b', { media_id: 'm1', score: 0.9 }), check('c', { media_id: 'm2' })])
    expect([...byMedia].map(([m, c]) => [m, c.id])).toEqual([['m1', 'b'], ['m2', 'c']])
  })
})
