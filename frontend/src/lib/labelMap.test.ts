import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../config/supabase', () => ({
  supabase: { auth: { getSession: async () => ({ data: { session: { access_token: 't' } } }) } },
}))

import { ApiError } from './apiClient'
import { labelMapProblems, saveLabelMap } from './labelMap'

const MAP = { person: { role: 'target', predicts: 'type', observation_type: 'human', threshold: 0.7 } }

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => { vi.unstubAllGlobals() })

describe('saveLabelMap', () => {
  it('PUTs the whole map, thresholds included, and returns null when saved', async () => {
    const fetchMock = respond(200, { data: { label_map: MAP, problems: [] } })
    expect(await saveLabelMap('m1', MAP)).toBeNull()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/models\/m1\/label-map$/)
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ label_map: MAP })
    expect(init.headers.Authorization).toBe('Bearer t')
  })

  it('returns the problems per label when LM-10 refuses the map', async () => {
    respond(422, { detail: { message: 'The label map breaks LM-10; nothing was saved.', problems: { grooming: "LM-10: class 'grooming' predicts behaviour" } } })
    expect(await saveLabelMap('m1', MAP)).toEqual({ grooming: "LM-10: class 'grooming' predicts behaviour" })
  })

  it('throws any other refusal with its message', async () => {
    respond(403, { detail: "Only a manager of the model's organisation can change its label map." })
    await expect(saveLabelMap('m1', MAP)).rejects.toThrow(/Only a manager/)
  })
})

describe('labelMapProblems', () => {
  it('reads problems only from an ApiError whose detail carries them', () => {
    expect(labelMapProblems(new ApiError('UNKNOWN', 'x', false, { problems: { a: 'bad', b: 3 } }))).toEqual({ a: 'bad' })
    expect(labelMapProblems(new ApiError('UNKNOWN', 'x'))).toBeNull()
    expect(labelMapProblems(new ApiError('UNKNOWN', 'x', false, { problems: ['a'] }))).toBeNull()
    expect(labelMapProblems(new Error('x'))).toBeNull()
  })
})
