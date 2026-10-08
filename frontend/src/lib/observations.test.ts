import { describe, expect, it } from 'vitest'
import { groupByBox, photoVerdict } from './observations'

const box = (id: string, x: number | null, y = 0.2, w = 0.3, h = 0.4) => ({ id, bbox_x: x, bbox_y: y, bbox_w: w, bbox_h: h })

describe('groupByBox', () => {
  it('puts a detection and its per-crop classifier row on one box', () => {
    const speciesnet = box('speciesnet', 0.1)
    const bioclip = box('bioclip', 0.1)
    const other = box('other animal', 0.6)
    expect(groupByBox([speciesnet, other, bioclip]).map(g => g.map(o => o.id)))
      .toEqual([['speciesnet', 'bioclip'], ['other animal']])
  })

  it('treats float noise below 1e-4 as the same box', () => {
    expect(groupByBox([box('a', 0.1), box('b', 0.1 + 1e-6)])).toHaveLength(1)
  })

  it('leaves out rows without a full box', () => {
    expect(groupByBox([box('whole image', null), { id: 'partial', bbox_x: 0.1 }])).toEqual([])
  })
})

describe('photoVerdict', () => {
  const rat = { id: 'speciesnet', source_type: 'ai', review_status: 'ai_reviewed', observation_type: 'animal', scientific_name: 'Rattus rattus' }
  const snBlank = { id: 'speciesnet', source_type: 'ai', review_status: 'ai_reviewed', observation_type: 'blank', scientific_name: null }
  const gemini = { id: 'gemini', source_type: 'ai', review_status: 'ai_reviewed', observation_type: 'animal', scientific_name: null }
  const consensus = (type: string) => ({ id: 'consensus', source_type: 'consensus', review_status: 'ai_reviewed', observation_type: type, scientific_name: null })

  it('keeps the first row when there is no consensus row', () => {
    expect(photoVerdict([snBlank, gemini])).toEqual({ labelObs: snBlank, isEmpty: true })
    expect(photoVerdict([rat, snBlank])).toEqual({ labelObs: rat, isEmpty: false })
    expect(photoVerdict([])).toEqual({ labelObs: null, isEmpty: false })
  })

  it('shows Empty when the consensus says blank over a SpeciesNet animal', () => {
    expect(photoVerdict([rat, consensus('blank')])).toEqual({ labelObs: null, isEmpty: true })
  })

  it('is not Empty when the consensus says animal over a SpeciesNet blank', () => {
    expect(photoVerdict([snBlank, gemini, consensus('animal')])).toEqual({ labelObs: null, isEmpty: false })
  })

  it('names a consensus animal from the first named per-model row', () => {
    expect(photoVerdict([consensus('animal'), snBlank, rat])).toEqual({ labelObs: rat, isEmpty: false })
  })

  it('lets a human verdict overrule the consensus', () => {
    const humanBlank = { id: 'human', source_type: 'human', review_status: 'human_reviewed', observation_type: 'blank', scientific_name: null }
    expect(photoVerdict([rat, consensus('animal'), humanBlank])).toEqual({ labelObs: humanBlank, isEmpty: true })
    const corrected = { ...rat, review_status: 'human_reviewed', scientific_name: 'Rattus norvegicus' }
    expect(photoVerdict([consensus('blank'), corrected])).toEqual({ labelObs: corrected, isEmpty: false })
  })

  it('counts consensus_approved as a human verdict, unlike the ai_reviewed consensus row', () => {
    const approvedBlank = { ...snBlank, review_status: 'consensus_approved' }
    expect(photoVerdict([gemini, consensus('animal'), approvedBlank])).toEqual({ labelObs: approvedBlank, isEmpty: true })
  })

  it('labels with a reviewed per-model row over a confirmed consensus row', () => {
    const confirmed = { ...consensus('animal'), review_status: 'human_reviewed' }
    const reviewedRat = { ...rat, review_status: 'human_reviewed' }
    expect(photoVerdict([confirmed, reviewedRat])).toEqual({ labelObs: reviewedRat, isEmpty: false })
  })
})
