// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest'
import { mediaDeleteOutcome } from './mediaDelete'

describe('mediaDeleteOutcome', () => {
  it('reports every photo deleted', () => {
    const out = mediaDeleteOutcome(['a', 'b'], { deleted_at: 'ts', deleted_ids: ['a', 'b'], skipped_ids: [] })
    expect(out).toEqual({ deletedIds: ['a', 'b'], skipped: 0, deletedAt: 'ts', message: 'Deleted 2 photos' })
  })

  it('names the rule when some photos were skipped', () => {
    const out = mediaDeleteOutcome(['a', 'b', 'c'], { deleted_at: 'ts', deleted_ids: ['a'], skipped_ids: ['b', 'c'] })
    expect(out.deletedIds).toEqual(['a'])
    expect(out.skipped).toBe(2)
    expect(out.message).toBe('Deleted 1 photo. 2 photos not deleted: only the uploader or a project admin can delete them')
  })

  it('says nothing was deleted when every photo was skipped', () => {
    const out = mediaDeleteOutcome(['a'], { deleted_at: 'ts', deleted_ids: [], skipped_ids: ['a'] })
    expect(out.deletedIds).toEqual([])
    expect(out.message).toBe('No photos were deleted: only the uploader or a project admin can delete them')
  })

  it('assumes all were deleted when the API does not list ids', () => {
    const out = mediaDeleteOutcome(['a', 'b'], { deleted_at: 'ts' })
    expect(out.deletedIds).toEqual(['a', 'b'])
    expect(out.skipped).toBe(0)
  })

  it('has no Undo timestamp without a response', () => {
    expect(mediaDeleteOutcome(['a'], undefined).deletedAt).toBeNull()
  })
})
