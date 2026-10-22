import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  confirmLabel, labelOf, planVerdict, removeHumanRow, updateHumanRow, verdictFields, verdictIsReviewed,
  writeVerdict, writeVerdicts, type VerdictRow,
} from './humanVerdict'
import { NOT_REMOVED, NOT_SAVED } from './observationWrites'

type Result = { data: unknown; error: { message: string; code?: string } | null }
interface Call { op: 'insert' | 'update' | 'delete'; payload?: unknown; ids?: string[] }

/** A client that records each insert, update and delete and answers from `answer`. */
function fakeDb(answer: (call: Call) => Result) {
  const calls: Call[] = []
  const db = {
    from: () => {
      const call: Call = { op: 'insert' }
      const chain = {
        insert: (rows: unknown) => { call.op = 'insert'; call.payload = rows; return chain },
        update: (patch: unknown) => { call.op = 'update'; call.payload = patch; return chain },
        delete: () => { call.op = 'delete'; return chain },
        in: (_col: string, ids: string[]) => { call.ids = ids; return chain },
        select: () => { calls.push(call); return Promise.resolve(answer(call)) },
      }
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, calls }
}

/** Answers every write as the database would for a member: each row comes back. */
const member = (call: Call): Result => {
  if (call.op === 'insert') return { data: (call.payload as object[]).map((r, i) => ({ id: `new${i}`, ...r })), error: null }
  return { data: (call.ids ?? []).map(id => ({ id })), error: null }
}
/** Answers as RLS does for a viewer: inserts fail, updates and deletes match nothing. */
const viewer = (call: Call): Result =>
  call.op === 'insert'
    ? { data: null, error: { code: '42501', message: 'new row violates row-level security policy' } }
    : { data: [], error: null }

const actor = { userId: 'u1', userEmail: 'me@example.org' }
const speciesnet: VerdictRow = { id: 'sn', source_type: 'ai', review_status: 'ai_reviewed', observation_type: 'animal', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat' }
const consensusBlank: VerdictRow = { id: 'cons', source_type: 'consensus', review_status: 'ai_reviewed', observation_type: 'blank', scientific_name: null }
const mine: VerdictRow = { id: 'h1', source_type: 'human', review_status: 'human_reviewed', observation_type: 'animal', scientific_name: 'Mus musculus', vernacular_name: 'House mouse', taxon_id: 't-mouse', count: 2 }
const photo = (...observations: VerdictRow[]) => ({ id: 'm1', deployment_id: 'd1', observations })

describe('verdictFields', () => {
  it('keeps species and count for an animal', () => {
    expect(verdictFields({ observation_type: 'animal', taxon_id: 't1', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat', count: 3 }))
      .toEqual({ observation_type: 'animal', taxon_id: 't1', scientific_name: 'Rattus rattus', vernacular_name: 'Black rat', count: 3 })
  })

  it('gives a person a count but no species', () => {
    expect(verdictFields({ observation_type: 'human', scientific_name: 'Homo sapiens', count: 2 }))
      .toMatchObject({ observation_type: 'human', taxon_id: null, scientific_name: null, count: 2 })
  })

  it('clears species, count and box on a blank', () => {
    expect(verdictFields({ observation_type: 'blank', scientific_name: 'Rattus rattus', count: 4 })).toEqual({
      observation_type: 'blank', taxon_id: null, scientific_name: null, vernacular_name: null, count: null,
      bbox_x: null, bbox_y: null, bbox_w: null, bbox_h: null,
    })
  })

  it('drops a count below one or not whole', () => {
    expect(verdictFields({ observation_type: 'animal', count: 0 }).count).toBeNull()
    expect(verdictFields({ observation_type: 'animal', count: 1.5 }).count).toBeNull()
  })
})

describe('labelOf and confirmLabel', () => {
  it('copies a model row as an animal label', () => {
    expect(labelOf(speciesnet)).toEqual({ observation_type: 'animal', taxon_id: null, scientific_name: 'Rattus rattus', vernacular_name: 'Black rat', count: null })
  })

  it('confirms the verdict the card shows', () => {
    expect(confirmLabel([speciesnet])?.scientific_name).toBe('Rattus rattus')
    expect(confirmLabel([speciesnet, consensusBlank])).toEqual({ observation_type: 'blank' })
    expect(confirmLabel([])).toBeNull()
  })

  it('knows when a person already ruled', () => {
    expect(verdictIsReviewed([speciesnet])).toBe(false)
    expect(verdictIsReviewed([speciesnet, mine])).toBe(true)
  })
})

describe('planVerdict', () => {
  it('inserts a human row on a photo without one, with human provenance', () => {
    const plan = planVerdict(photo(speciesnet), { observation_type: 'blank' }, actor)
    expect(plan.kind).toBe('insert')
    expect(plan.kind === 'insert' && plan.row).toMatchObject({
      deployment_id: 'd1', media_id: 'm1', observation_level: 'media', observation_type: 'blank',
      source_type: 'human', review_status: 'human_reviewed', annotator_id: 'u1', reviewer_id: 'u1',
    })
  })

  it('updates the human rows and never an AI row', () => {
    const extra = { ...mine, id: 'h2' }
    const plan = planVerdict(photo(speciesnet, mine, consensusBlank, extra), { observation_type: 'animal', scientific_name: 'Rattus rattus' }, actor)
    expect(plan).toMatchObject({ kind: 'update', ids: ['h1', 'h2'] })
    expect(plan.kind === 'update' && plan.patch).toMatchObject({ scientific_name: 'Rattus rattus', reviewer_id: 'u1' })
    expect(plan.kind === 'update' && plan.patch).not.toHaveProperty('source_type')
  })
})

describe('writeVerdict', () => {
  it('adds the human row beside the AI row, and undo deletes it', async () => {
    const { db, calls } = fakeDb(member)
    const result = await writeVerdict(db, photo(speciesnet), { observation_type: 'blank' }, actor)
    expect(result.observations.map(o => o.id)).toEqual(['sn', 'new0'])
    expect(result.observations[0]).toBe(speciesnet)
    expect(result.rowId).toBe('new0')
    expect(await result.undo()).toEqual([speciesnet])
    expect(calls.map(c => c.op)).toEqual(['insert', 'delete'])
    expect(calls[1].ids).toEqual(['new0'])
  })

  it('updates the existing human row, and undo puts its old label back', async () => {
    const { db, calls } = fakeDb(member)
    const result = await writeVerdict(db, photo(speciesnet, mine), { observation_type: 'animal', scientific_name: 'Rattus rattus', taxon_id: 't-rat' }, actor)
    expect(calls[0]).toMatchObject({ op: 'update', ids: ['h1'] })
    expect(result.observations.find(o => o.id === 'h1')).toMatchObject({ scientific_name: 'Rattus rattus', taxon_id: 't-rat' })
    expect(result.observations.find(o => o.id === 'sn')).toBe(speciesnet)
    await result.undo()
    expect(calls[1]).toMatchObject({ op: 'update', ids: ['h1'] })
    expect(calls[1].payload).toMatchObject({ scientific_name: 'Mus musculus', taxon_id: 't-mouse', count: 2, review_status: 'human_reviewed' })
  })

  it('reports a refused insert or a 0-row update as not saved', async () => {
    const { db } = fakeDb(viewer)
    await expect(writeVerdict(db, photo(speciesnet), { observation_type: 'blank' }, actor)).rejects.toThrow(NOT_SAVED)
    await expect(writeVerdict(db, photo(mine), { observation_type: 'blank' }, actor)).rejects.toThrow(NOT_SAVED)
  })
})

describe('writeVerdicts', () => {
  it('inserts for photos without a human row and updates the rest, one request each', async () => {
    const { db, calls } = fakeDb(member)
    const a = { id: 'a', deployment_id: 'd1', observations: [speciesnet] }
    const b = { id: 'b', deployment_id: 'd1', observations: [{ ...mine, id: 'hb' }] }
    const result = await writeVerdicts(db, [a, b], { observation_type: 'animal', scientific_name: 'Rattus rattus' }, actor)
    expect(calls.map(c => c.op)).toEqual(['insert', 'update'])
    expect((calls[0].payload as object[]).length).toBe(1)
    expect(calls[1].ids).toEqual(['hb'])
    expect(result.saved).toBe(2)
    await result.undo()
    expect(calls.slice(2).map(c => c.op)).toEqual(['delete', 'update'])
  })

  it('takes the inserts back when the update is refused', async () => {
    const { db, calls } = fakeDb(call => call.op === 'update' ? { data: [], error: null } : member(call))
    const a = { id: 'a', deployment_id: 'd1', observations: [speciesnet] }
    const b = { id: 'b', deployment_id: 'd2', observations: [{ ...mine, id: 'hb' }] }
    await expect(writeVerdicts(db, [a, b], { observation_type: 'blank' }, actor)).rejects.toThrow(NOT_SAVED)
    expect(calls.map(c => c.op)).toEqual(['insert', 'update', 'delete'])
  })
})

describe('updateHumanRow and removeHumanRow', () => {
  it('refuses to change an AI row', async () => {
    const { db, calls } = fakeDb(member)
    await expect(updateHumanRow(db, photo(speciesnet), 'sn', { bbox_x: 0.1 }, actor)).rejects.toThrow()
    await expect(removeHumanRow(db, photo(speciesnet), 'sn')).rejects.toThrow()
    expect(calls).toEqual([])
  })

  it('removes a person row and re-inserts it in full on undo', async () => {
    const stored = { ...mine, created_at: '2026-10-01T00:00:00Z', classification_timestamp: '2026-10-01T00:00:00Z' }
    const { db, calls } = fakeDb(call => call.op === 'delete' ? { data: [stored], error: null } : member(call))
    const result = await removeHumanRow(db, photo(speciesnet, mine), 'h1')
    expect(result.observations).toEqual([speciesnet])
    await result.undo()
    expect(calls[1]).toMatchObject({ op: 'insert', payload: [stored] })
  })

  it('reports a delete that matched nothing as not removed', async () => {
    const { db } = fakeDb(viewer)
    await expect(removeHumanRow(db, photo(mine), 'h1')).rejects.toThrow(NOT_REMOVED)
  })
})
