import { describe, expect, it, vi } from 'vitest'
import { fetchGbifDatasetKey, gbifDatasetUrl, saveGbifDataset } from './gbifDataset'

const KEY = '423e89b5-3c05-4ebe-8671-ddade9409b11'

function db(result: { data: unknown; error: unknown }) {
  const maybeSingle = vi.fn().mockResolvedValue(result)
  const eq = vi.fn(() => ({ maybeSingle }))
  const select = vi.fn(() => ({ eq }))
  return { client: { from: vi.fn(() => ({ select })) } as never, select, eq }
}

describe('fetchGbifDatasetKey', () => {
  it('reads the key of the project', async () => {
    const d = db({ data: { gbif_dataset_key: KEY }, error: null })
    await expect(fetchGbifDatasetKey(d.client, 'p1')).resolves.toBe(KEY)
    expect(d.select).toHaveBeenCalledWith('gbif_dataset_key')
    expect(d.eq).toHaveBeenCalledWith('id', 'p1')
  })

  it('is null without a key or a visible project, and throws on an error', async () => {
    await expect(fetchGbifDatasetKey(db({ data: { gbif_dataset_key: null }, error: null }).client, 'p1')).resolves.toBeNull()
    await expect(fetchGbifDatasetKey(db({ data: null, error: null }).client, 'p1')).resolves.toBeNull()
    await expect(fetchGbifDatasetKey(db({ data: null, error: { message: 'boom' } }).client, 'p1')).rejects.toThrow('boom')
  })
})

describe('saveGbifDataset', () => {
  it('sends the text, and blank text as null to clear', async () => {
    const put = vi.fn().mockResolvedValue({ data: { gbif_dataset_key: KEY, dataset: { title: 'Ridge', doi: null } } })
    await expect(saveGbifDataset(put, 'p1', ` ${gbifDatasetUrl(KEY)} `)).resolves.toMatchObject({ gbif_dataset_key: KEY })
    expect(put).toHaveBeenCalledWith('/api/projects/p1/gbif-dataset', { dataset: `https://www.gbif.org/dataset/${KEY}` })
    await saveGbifDataset(put, 'p1', '  ')
    expect(put).toHaveBeenLastCalledWith('/api/projects/p1/gbif-dataset', { dataset: null })
  })

  it('passes the refusal on', async () => {
    const put = vi.fn().mockRejectedValue(new Error('Not saved: only a project admin can change the GBIF dataset.'))
    await expect(saveGbifDataset(put, 'p1', KEY)).rejects.toThrow(/project admin/)
  })
})
