import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  buildDetailsPatch, fetchProjectDetails, formFromDetails, NOT_ALLOWED, saveProjectDetails, validateDetails,
  type ProjectDetails,
} from './projectDetails'

const stored: ProjectDetails = { id: 'p-1', name: 'Ridge rats', description: null, website: null }

/** A projects query builder that records its calls and resolves to `answer`. */
function fakeDb(answer: { data: unknown; error: unknown }) {
  const calls: unknown[][] = []
  const builder: Record<string, unknown> = {
    then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => Promise.resolve(answer).then(resolve, reject),
    maybeSingle: () => Promise.resolve(answer),
  }
  for (const m of ['select', 'update', 'eq']) {
    builder[m] = (...args: unknown[]) => { calls.push([m, ...args]); return builder }
  }
  const from = vi.fn(() => builder)
  return { db: { from } as unknown as SupabaseClient, calls, from }
}

describe('form helpers', () => {
  it('shows nulls as empty fields', () => {
    expect(formFromDetails(stored)).toEqual({ name: 'Ridge rats', description: '', website: '' })
  })

  it('requires a name', () => {
    expect(validateDetails({ name: '  ', description: '', website: '' })).toBe('A project name is required.')
    expect(validateDetails({ name: 'A', description: '', website: '' })).toBeNull()
  })

  it('trims, and saves a blank description or website as null', () => {
    expect(buildDetailsPatch({ name: ' Ridge rats ', description: '  ', website: ' https://example.org ' }))
      .toEqual({ name: 'Ridge rats', description: null, website: 'https://example.org' })
  })
})

describe('fetchProjectDetails', () => {
  it('reads the row by id, null when the user cannot see it', async () => {
    const { db, calls } = fakeDb({ data: stored, error: null })
    await expect(fetchProjectDetails(db, 'p-1')).resolves.toEqual(stored)
    expect(calls).toContainEqual(['eq', 'id', 'p-1'])
    await expect(fetchProjectDetails(fakeDb({ data: null, error: null }).db, 'p-1')).resolves.toBeNull()
  })
})

describe('saveProjectDetails', () => {
  const patch = { name: 'Ridge rats 2', description: 'Trial', website: null }

  it('updates as the user, asks for the row back and returns it', async () => {
    const saved = { id: 'p-1', ...patch }
    const { db, calls, from } = fakeDb({ data: [saved], error: null })
    await expect(saveProjectDetails(db, 'p-1', patch)).resolves.toEqual(saved)
    expect(from).toHaveBeenCalledWith('projects')
    expect(calls).toEqual([['update', patch], ['eq', 'id', 'p-1'], ['select', 'id, name, description, website']])
  })

  it('treats 0 rows as refused, since RLS does not raise an error', async () => {
    await expect(saveProjectDetails(fakeDb({ data: [], error: null }).db, 'p-1', patch)).rejects.toThrow(NOT_ALLOWED)
  })

  it('shows the database error as a plain message', async () => {
    await expect(saveProjectDetails(fakeDb({ data: null, error: { code: '23502', message: 'null value in column "name"' } }).db, 'p-1', patch))
      .rejects.toThrow('Not saved: null value in column "name"')
    await expect(saveProjectDetails(fakeDb({ data: null, error: { code: '42501', message: 'new row violates row-level security policy' } }).db, 'p-1', patch))
      .rejects.toThrow(NOT_ALLOWED)
  })
})
