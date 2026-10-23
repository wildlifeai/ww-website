import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { fetchMediaThumbs, THUMB_CHUNK } from './mediaThumbs'

/** A client that answers each `in('id', ...)` with the rows `rowsFor` gives for those ids. */
function fakeDb(rowsFor: (ids: string[]) => unknown[], error: { message: string } | null = null) {
  const calls: string[][] = []
  const db = {
    from: vi.fn(() => {
      let ids: string[] = []
      const chain: Record<string, unknown> = {}
      chain.select = vi.fn(() => chain)
      chain.in = vi.fn((_col: string, v: string[]) => { ids = v; calls.push(v); return chain })
      chain.is = vi.fn(() => chain)
      chain.then = (resolve: (r: unknown) => unknown) =>
        Promise.resolve({ data: error ? null : rowsFor(ids), error }).then(resolve)
      return chain
    }),
  }
  return { db: db as unknown as SupabaseClient, calls }
}

const row = (id: string, thumb: string | null, filePath = 'gdrive://x/' + id) =>
  ({ id, file_name: `${id}.jpg`, file_path: filePath, media_assets: { thumbnail_url: thumb, preview_url: null } })

describe('fetchMediaThumbs', () => {
  it('maps each photo to its thumbnail, null when there is none to show', async () => {
    const { db } = fakeDb(() => [row('a', 'https://cdn/a.jpg'), row('b', null)])
    expect(await fetchMediaThumbs(db, ['a', 'b'])).toEqual({ a: 'https://cdn/a.jpg', b: null })
  })

  it('asks once per chunk of ids and drops duplicates', async () => {
    const ids = Array.from({ length: THUMB_CHUNK + 5 }, (_, i) => `m${i}`)
    const { db, calls } = fakeDb(chunk => chunk.map(id => row(id, `https://cdn/${id}`)))
    const out = await fetchMediaThumbs(db, [...ids, 'm0'])
    expect(calls.map(c => c.length)).toEqual([THUMB_CHUNK, 5])
    expect(Object.keys(out)).toHaveLength(THUMB_CHUNK + 5)
  })

  it('makes no request for no ids', async () => {
    const { db, calls } = fakeDb(() => [])
    expect(await fetchMediaThumbs(db, [])).toEqual({})
    expect(calls).toEqual([])
  })

  it('passes a database error through', async () => {
    const { db } = fakeDb(() => [], { message: 'boom' })
    await expect(fetchMediaThumbs(db, ['a'])).rejects.toThrow('boom')
  })
})
