import { describe, expect, it } from 'vitest'
import { orderClusters, pageOf } from './clusterReview'

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
