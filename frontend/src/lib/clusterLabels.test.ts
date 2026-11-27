import { describe, expect, it } from 'vitest'
import type { ClusterLabelCount, ClusterLabels } from '../hooks/useBrain'
import { confirmWarning, labelChanges, mixedTitle, warningText } from './clusterLabels'

const label = (scientific_name: string, photos: number, unreviewed: number, vernacular_name: string | null = null): ClusterLabelCount =>
  ({ scientific_name, vernacular_name, taxon_id: null, photos, unreviewed })

const mixed: ClusterLabels = {
  id: 'c1',
  mixed: true,
  labels: [label('Rattus rattus', 12, 10, 'Ship rat'), label('Rattus norvegicus', 3, 3, 'Norway rat'), label('Mus musculus', 2, 0, 'House mouse')],
}

describe('labelChanges', () => {
  it('lists the unreviewed members whose species is another name, and skips those a person labelled', () => {
    expect(labelChanges(mixed, { scientific_name: 'rattus  Norvegicus' })).toEqual([{ name: 'Ship rat', photos: 10 }])
    expect(labelChanges(mixed, { scientific_name: 'Felis catus' })).toEqual([{ name: 'Ship rat', photos: 10 }, { name: 'Norway rat', photos: 3 }])
  })

  it('is empty while the labels are unknown', () => {
    expect(labelChanges(undefined, { scientific_name: 'Rattus rattus' })).toEqual([])
  })
})

describe('confirmWarning', () => {
  it('warns on a mixed group when labels change', () => {
    expect(confirmWarning(mixed, { scientific_name: 'Rattus rattus' })).toEqual([{ name: 'Norway rat', photos: 3 }])
  })

  it('confirms at once on a group that is not mixed, or when nothing would change', () => {
    expect(confirmWarning({ ...mixed, mixed: false }, { scientific_name: 'Felis catus' })).toBeNull()
    const onlyPeople: ClusterLabels = { id: 'c2', mixed: true, labels: [label('Rattus rattus', 5, 5), label('Mus musculus', 2, 0)] }
    expect(confirmWarning(onlyPeople, { scientific_name: 'Rattus rattus' })).toBeNull()
    expect(confirmWarning(undefined, { scientific_name: 'Rattus rattus' })).toBeNull()
  })
})

describe('warningText', () => {
  it('names each label that changes and the species it becomes', () => {
    expect(warningText([{ name: 'Ship rat', photos: 12 }, { name: 'House mouse', photos: 3 }], 'Norway rat'))
      .toBe('Mixed group: 12 Ship rat and 3 House mouse photos would become Norway rat.')
    expect(warningText([{ name: 'A', photos: 1 }, { name: 'B', photos: 1 }, { name: 'C', photos: 2 }], 'D'))
      .toBe('Mixed group: 1 A, 1 B and 2 C photos would become D.')
    expect(warningText([{ name: 'Ship rat', photos: 1 }], 'Norway rat')).toBe('Mixed group: 1 Ship rat photo would become Norway rat.')
  })
})

describe('mixedTitle', () => {
  it('lists each species with its photos', () => {
    expect(mixedTitle(mixed)).toBe('Ship rat: 12, Norway rat: 3, House mouse: 2')
  })
})
