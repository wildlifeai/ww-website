import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { checkEui, EUI_FORMAT, findEuiOwner, NOT_ALLOWED, saveDeviceEui } from './deviceEui'

/**
 * A devices client: the first `from('devices')` is the duplicate read, the second the update.
 * Each records its calls and resolves to its answer.
 */
function fakeDb(...answers: { data: unknown; error: unknown }[]) {
  const calls: unknown[][][] = []
  const from = vi.fn(() => {
    const answer = answers[calls.length] ?? { data: null, error: null }
    const mine: unknown[][] = []
    calls.push(mine)
    const builder: Record<string, unknown> = {
      then: (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) => Promise.resolve(answer).then(resolve, reject),
    }
    for (const m of ['select', 'ilike', 'neq', 'limit', 'update', 'eq']) {
      builder[m] = (...args: unknown[]) => { mine.push([m, ...args]); return builder }
    }
    return builder
  })
  return { db: { from } as unknown as SupabaseClient, calls, from }
}

describe('checkEui', () => {
  it('stores 16 hex digits, upper case, with colons, dashes and spaces dropped', () => {
    expect(checkEui('70b3d57ed0012345')).toEqual({ eui: '70B3D57ED0012345' })
    expect(checkEui('70:B3:D5:7E:D0:01:23:45')).toEqual({ eui: '70B3D57ED0012345' })
    expect(checkEui(' 70-b3-d5-7e-d0-01-23-45 ')).toEqual({ eui: '70B3D57ED0012345' })
  })

  it('refuses anything else', () => {
    for (const bad of ['', '70B3D57ED00123', '70B3D57ED001234567', '70B3D57ED001234G', 'EUI_NZ_001']) {
      expect(checkEui(bad)).toEqual({ error: EUI_FORMAT })
    }
  })

  it('refuses all zeros and the firmware default, as the app does', () => {
    expect(checkEui('00:00:00:00:00:00:00:00')).toEqual({ error: "All zeros is not a camera's own DevEUI." })
    expect(checkEui('00:80:e1:01:01:01:01:01'))
      .toEqual({ error: "0080E10101010101 is the firmware's shared default, not this camera's own." })
  })
})

describe('findEuiOwner', () => {
  it('looks for another camera with the EUI in any case', async () => {
    const { db, calls } = fakeDb({ data: [{ id: 'd-2', name: 'WILD-AB12' }], error: null })
    await expect(findEuiOwner(db, '70B3D57ED0012345', 'd-1')).resolves.toEqual({ id: 'd-2', name: 'WILD-AB12' })
    expect(calls[0]).toEqual([
      ['select', 'id, name'], ['ilike', 'device_eui', '70B3D57ED0012345'], ['neq', 'id', 'd-1'], ['limit', 1],
    ])
  })
})

describe('saveDeviceEui', () => {
  it('checks for a duplicate, then saves the stored form as the user and returns it', async () => {
    const { db, calls } = fakeDb({ data: [], error: null }, { data: [{ id: 'd-1', device_eui: '70B3D57ED0012345' }], error: null })
    await expect(saveDeviceEui(db, 'd-1', '70:b3:d5:7e:d0:01:23:45')).resolves.toBe('70B3D57ED0012345')
    expect(calls[1]).toEqual([['update', { device_eui: '70B3D57ED0012345' }], ['eq', 'id', 'd-1'], ['select', 'id, device_eui']])
  })

  it('refuses a bad EUI before any read', async () => {
    const { db, from } = fakeDb()
    await expect(saveDeviceEui(db, 'd-1', '0080E10101010101')).rejects.toThrow("firmware's shared default")
    expect(from).not.toHaveBeenCalled()
  })

  it('refuses an EUI another live camera has, without writing', async () => {
    const { db, calls } = fakeDb({ data: [{ id: 'd-2', name: 'WILD-AB12' }], error: null })
    await expect(saveDeviceEui(db, 'd-1', '70B3D57ED0012345')).rejects.toThrow('WILD-AB12 already has this DevEUI.')
    expect(calls).toHaveLength(1)
  })

  it('words a refusal by RLS (0 rows) or by the lock trigger (42501)', async () => {
    await expect(saveDeviceEui(fakeDb({ data: [], error: null }, { data: [], error: null }).db, 'd-1', '70B3D57ED0012345'))
      .rejects.toThrow(NOT_ALLOWED)
    const locked = fakeDb({ data: [], error: null }, { data: null, error: { code: '42501', message: 'Permission denied' } })
    await expect(saveDeviceEui(locked.db, 'd-1', '70B3D57ED0012345')).rejects.toThrow(NOT_ALLOWED)
  })
})
