import { describe, expect, it, vi } from 'vitest'
import { REVIEW_BATCH_MAX, confirmedText, orderClusters, pageOf, reviewInBatches, type ReviewBatchResult } from './clusterReview'

const c = (id: string, image_count: number, review_state = 'open', is_outlier_cluster = false, cluster_id = 0) =>
  ({ id, image_count, review_state, is_outlier_cluster, cluster_id })

describe('orderClusters', () => {
  const list = [
    c('small', 3),
    c('done', 50, 'confirmed'),
    c('outliers', 80, 'open', true, -1),
    c('big', 40),
    c('locked', 10, 'locked'),
  ]

  it('lists unconfirmed clusters biggest first, then the outliers, and hides confirmed ones', () => {
    expect(orderClusters(list, false).map(x => x.id)).toEqual(['big', 'locked', 'small', 'outliers'])
  })

  it('puts confirmed clusters last when asked for them', () => {
    expect(orderClusters(list, true).map(x => x.id)).toEqual(['big', 'locked', 'small', 'outliers', 'done'])
  })

  it('keeps a cluster confirmed during this visit where it was', () => {
    const now = list.map(x => (x.id === 'big' ? { ...x, review_state: 'confirmed' } : x))
    expect(orderClusters(now, false, new Set(['big'])).map(x => x.id)).toEqual(['big', 'locked', 'small', 'outliers'])
    expect(orderClusters(now, false).map(x => x.id)).toEqual(['locked', 'small', 'outliers'])
  })

  it('breaks a size tie by cluster id and does not change the input', () => {
    const tie = [c('b', 5, 'open', false, 2), c('a', 5, 'open', false, 1)]
    expect(orderClusters(tie, false).map(x => x.id)).toEqual(['a', 'b'])
    expect(tie.map(x => x.id)).toEqual(['b', 'a'])
  })
})

describe('pageOf', () => {
  const items = Array.from({ length: 23 }, (_, i) => i)

  it('slices one page and says where it is', () => {
    expect(pageOf(items, 1, 10)).toEqual({ items: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19], page: 1, pages: 3, first: 11, last: 20 })
  })

  it('clamps a page past the end to the last page', () => {
    const p = pageOf(items, 5, 10)
    expect(p.page).toBe(2)
    expect(p.items).toEqual([20, 21, 22])
    expect([p.first, p.last]).toEqual([21, 23])
  })

  it('has one empty page for an empty list', () => {
    expect(pageOf([], 0, 10)).toEqual({ items: [], page: 0, pages: 1, first: 0, last: 0 })
  })
})

describe('reviewInBatches', () => {
  const answer = (ids: string[], skip = 0): ReviewBatchResult => ({
    created: ids.length - skip, updated: 0, observation_ids: [], skipped: ids.slice(0, skip).map(media_id => ({ media_id, reason: 'not_writable' })),
  })

  it('sends a selection of up to 500 photos in one request', async () => {
    const send = vi.fn(async (ids: string[]) => answer(ids))
    const ids = Array.from({ length: REVIEW_BATCH_MAX }, (_, i) => `m${i}`)
    expect(await reviewInBatches(ids, send)).toEqual({ saved: 500, skipped: 0 })
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('splits a bigger selection at the cap and adds up the answers', async () => {
    const send = vi.fn(async (ids: string[]) => ({ ...answer(ids, 1), updated: 2, created: ids.length - 3 }))
    const ids = Array.from({ length: 1201 }, (_, i) => `m${i}`)
    expect(await reviewInBatches(ids, send)).toEqual({ saved: 1201 - 3, skipped: 3 })
    expect(send.mock.calls.map(([part]) => part.length)).toEqual([500, 500, 201])
  })

  it('rejects when a request is refused', async () => {
    const send = vi.fn(async () => { throw new Error('You need the Project Member role to change this project.') })
    await expect(reviewInBatches(['m1'], send)).rejects.toThrow('Project Member')
  })
})

describe('confirmedText', () => {
  it('names the species and the photos labelled', () => {
    expect(confirmedText(1, 0, 'Ship rat')).toBe('Confirmed 1 photo as Ship rat')
    expect(confirmedText(40, 0, 'Ship rat')).toBe('Confirmed 40 photos as Ship rat')
  })

  it('says how many photos it skipped because a person had labelled them', () => {
    expect(confirmedText(38, 2, 'Ship rat')).toBe('Confirmed 38 photos as Ship rat; skipped 2 photos a person had labelled')
    expect(confirmedText(0, 1, 'Ship rat')).toBe('Confirmed 0 photos as Ship rat; skipped 1 photo a person had labelled')
  })
})
