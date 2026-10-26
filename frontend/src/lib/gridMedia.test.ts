import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  fetchMediaInOrder, isLabelCard, labelCards, MAX_LABEL_CARDS, MEDIA_SELECT, pageStats, pendingMediaRecord, selectableInOrder,
  type MediaRecord, type ObservationRecord,
} from './gridMedia'

const obs = (over: Partial<ObservationRecord> = {}): ObservationRecord => ({
  id: 'o', deployment_id: 'd', media_id: 'm', observation_type: 'animal', scientific_name: null, count: null,
  life_stage: null, sex: null, behavior: null, classification_method: 'machine', classified_by: null,
  classification_probability: null, observation_comments: null, ...over,
})

const photo = (id: string, over: Partial<MediaRecord> = {}): MediaRecord => ({
  id, deployment_id: 'd', file_path: `p/${id}.jpg`, file_name: `${id}.jpg`, file_mediatype: 'image/jpeg', timestamp: null,
  file_public: false, media_comments: null, exif_metadata: null, media_assets: null, observations: [], ...over,
})

/** A client whose media reads answer from `rows`, recording the ids each request asked for. */
function fakeDb(rows: MediaRecord[], error: string | null = null) {
  const requests: string[][] = []
  const selects: string[] = []
  const db = {
    from: (table: string) => {
      expect(table).toBe('media')
      return {
        select: (cols: string) => {
          selects.push(cols)
          return {
            in: (column: string, ids: string[]) => {
              expect(column).toBe('id')
              requests.push(ids)
              return {
                is: (col: string, value: null) => {
                  expect([col, value]).toEqual(['deleted_at', null])
                  return Promise.resolve(error
                    ? { data: null, error: { message: error } }
                    : { data: rows.filter(r => ids.includes(r.id)), error: null })
                },
              }
            },
          }
        },
      }
    },
  }
  return { db: db as unknown as SupabaseClient, requests, selects }
}

describe('fetchMediaInOrder', () => {
  it('returns the photos in the order asked, dropping ones that are gone', async () => {
    const { db, selects } = fakeDb([photo('b'), photo('a'), photo('c')])
    const got = await fetchMediaInOrder(db, ['c', 'x', 'a', 'b'])
    expect(got.map(m => m.id)).toEqual(['c', 'a', 'b'])
    expect(selects).toEqual([MEDIA_SELECT])
  })

  it('asks for 100 ids a request', async () => {
    const ids = Array.from({ length: 250 }, (_, i) => `m${i}`)
    const { db, requests } = fakeDb(ids.map(id => photo(id)))
    const got = await fetchMediaInOrder(db, ids)
    expect(requests.map(r => r.length)).toEqual([100, 100, 50])
    expect(got.map(m => m.id)).toEqual(ids)
  })

  it('asks nothing for no ids', async () => {
    const { db, requests } = fakeDb([])
    expect(await fetchMediaInOrder(db, [])).toEqual([])
    expect(requests).toEqual([])
  })

  it('throws the read error', async () => {
    const { db } = fakeDb([], 'permission denied')
    await expect(fetchMediaInOrder(db, ['a'])).rejects.toThrow('permission denied')
  })
})

describe('label cards', () => {
  it('shows AI rows with a crop and every human label, not cropless AI rows', () => {
    expect(isLabelCard(obs({ crop_url: 'c.jpg' }))).toBe(true)
    expect(isLabelCard(obs({ source_type: 'human', classification_method: 'human' }))).toBe(true)
    expect(isLabelCard(obs({ review_status: 'human_reviewed' }))).toBe(true)
    expect(isLabelCard(obs())).toBe(false)
  })

  it('keeps at most MAX_LABEL_CARDS a photo', () => {
    const many = Array.from({ length: MAX_LABEL_CARDS + 2 }, (_, i) => obs({ id: `o${i}`, crop_url: 'c.jpg' }))
    expect(labelCards(photo('a', { observations: [obs(), ...many] })).map(o => o.id)).toEqual(['o0', 'o1', 'o2', 'o3'])
  })
})

describe('page contents', () => {
  it('makes a display-only card for a file still uploading', () => {
    const p = pendingMediaRecord({ fileName: 'IMG_01.JPG', deploymentId: 'd1' })
    expect(p).toMatchObject({ id: 'pending:img_01.jpg', deployment_id: 'd1', file_name: 'IMG_01.JPG', file_path: '', _pending: true })
    expect(p.observations).toEqual([])
  })

  it('counts real photos only, so an upload in progress is not "no image"', () => {
    const cards = [photo('a'), photo('b', { file_path: '' }), pendingMediaRecord({ fileName: 'x.jpg', deploymentId: 'd' })]
    expect(pageStats(cards)).toEqual({ total: 2, uploading: 1, noImage: 1 })
  })

  it('orders a range over the selectable photos, and in the Labels view only those with a card', () => {
    const cards = [
      photo('a', { observations: [obs({ crop_url: 'c.jpg' })] }),
      photo('b'),
      photo('locked', { observations: [obs({ crop_url: 'c.jpg' })] }),
      photo('c', { observations: [obs({ source_type: 'human' })] }),
    ]
    const selectable = (m: MediaRecord) => m.id !== 'locked'
    expect(selectableInOrder(cards, selectable, false)).toEqual(['a', 'b', 'c'])
    expect(selectableInOrder(cards, selectable, true)).toEqual(['a', 'c'])
  })
})
