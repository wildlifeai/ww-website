import { describe, expect, it, vi } from 'vitest'
import { publishInRounds, type INatPublishResult } from './inatPublishRounds'

const answer = (over: Partial<INatPublishResult> = {}): INatPublishResult => ({
  observations_created: 0,
  photos_uploaded: 0,
  skipped_bycatch: 0,
  skipped_already_published: 0,
  errors: 0,
  observations: [],
  remaining_media_ids: [],
  ...over,
})

const obs = (id: number, photo_errors = 0) => ({ inat_observation_id: id, uri: null, species_guess: null, media_count: 1, photo_errors })

describe('publishInRounds', () => {
  it('sends what each round hands back and adds the rounds up', async () => {
    const post = vi.fn()
      .mockResolvedValueOnce(answer({
        observations_created: 1, photos_uploaded: 2, skipped_bycatch: 1, observations: [obs(1)],
        remaining_media_ids: ['a1', 'a2', 'a3', 'b1'],
      }))
      // The second round counts a1 and a2, published by the first, as already on iNaturalist.
      .mockResolvedValueOnce(answer({
        observations_created: 1, photos_uploaded: 2, skipped_already_published: 2, observations: [obs(1), obs(2)],
      }))

    const r = await publishInRounds(post, ['a1', 'a2', 'a3', 'b1', 'x'])

    expect(post.mock.calls).toEqual([[['a1', 'a2', 'a3', 'b1', 'x']], [['a1', 'a2', 'a3', 'b1']]])
    expect(r).toMatchObject({
      observations_created: 2, photos_uploaded: 4, skipped_bycatch: 1, skipped_already_published: 0,
      remaining_media_ids: [],
    })
    expect(r.observations).toHaveLength(3)
  })

  it('makes one call when the first round gets through', async () => {
    const post = vi.fn().mockResolvedValue(answer({ observations_created: 1, photos_uploaded: 1 }))
    await publishInRounds(post, ['a1'])
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('stops when a round did nothing, and keeps what was left', async () => {
    const post = vi.fn().mockResolvedValue(answer({ remaining_media_ids: ['a1', 'a2'] }))
    const r = await publishInRounds(post, ['a1', 'a2'])
    expect(post).toHaveBeenCalledTimes(1)
    expect(r.remaining_media_ids).toEqual(['a1', 'a2'])
  })

  it('stops after one round per photo', async () => {
    const post = vi.fn().mockResolvedValue(answer({ photos_uploaded: 1, remaining_media_ids: ['a1', 'a2'] }))
    const r = await publishInRounds(post, ['a1', 'a2'])
    expect(post).toHaveBeenCalledTimes(2)
    expect(r.remaining_media_ids).toEqual(['a1', 'a2'])
  })
})
