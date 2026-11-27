import { describe, expect, it } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  fetchDeploymentUpdate, fetchFieldDeployments, fetchLatestTelemetry, fetchLorawanDetections, firmwareText,
  hasLorawanCameras, isLorawanCamera, startOfLocalDay, sumDetections, temperatureText, withEntry, withLastSeen, DETECTION_HOURS, ID_CHUNK, NO_MESSAGE,
} from './realtimeReads'
import { batteryText, sdFreeText } from './lorawanFeed'
import { PAGE_SIZE } from './liveObservations'

type Call = [string, ...unknown[]]
type Answer = { data: unknown[] | null; error: { message: string } | null }

/**
 * A client that records each query's calls and answers in turn. A query ends at `range`, or
 * when the chain itself is awaited; `answer` picks the reply from the recorded calls.
 */
function fakeDb(answer: (calls: Call[], n: number) => Answer) {
  const queries: Call[][] = []
  const db = {
    from: (table: string) => {
      const calls: Call[] = [['from', table]]
      const n = queries.length
      queries.push(calls)
      const chain: Record<string, unknown> = {}
      for (const m of ['select', 'in', 'eq', 'is', 'or', 'order', 'gt', 'gte', 'not', 'limit']) {
        chain[m] = (...args: unknown[]) => { calls.push([m, ...args]); return chain }
      }
      chain.range = (...args: unknown[]) => {
        calls.push(['range', ...args])
        return Promise.resolve(answer(calls, n))
      }
      chain.then = (resolve: (v: Answer) => void) => resolve(answer(calls, n))
      return chain
    },
  }
  return { db: db as unknown as SupabaseClient, queries }
}

const latest = (deployment: string, at: string, battery: number | null = 80) => ({
  deployment_id: deployment, received_at: at, battery_level: battery, battery_mv: null, sd_free_pct: 41,
  temperature_c: null, health_state: null, health_reasons: null,
})
/** What `latest` reads as. */
const seen = (at: string, battery: number | null = 80) => ({
  ...NO_MESSAGE, lastSeen: at, batteryPct: battery, sdFreePct: 41,
})

const detected = (deployment: string, ...counts: (number | null)[]) => ({
  deployment_id: deployment, lorawan_parsed_messages: counts.map(nn_detections => ({ nn_detections })),
})

describe('fetchLatestTelemetry', () => {
  it('reads the view once for the deployments and maps each row', async () => {
    const { db, queries } = fakeDb(() => ({ data: [latest('d1', 't1'), latest('d3', 't2', null)], error: null }))
    expect(await fetchLatestTelemetry(db, ['d1', 'd2', 'd3'])).toEqual({ d1: seen('t1'), d3: seen('t2', null) })
    expect(queries).toEqual([[
      ['from', 'deployment_latest_lorawan'],
      ['select', 'deployment_id, received_at, battery_level, battery_mv, sd_free_pct, temperature_c, health_state, health_reasons'],
      ['in', 'deployment_id', ['d1', 'd2', 'd3']],
    ]])
  })

  it('reads the volts, the temperature, the health state and its reasons, and never sd_card_used_capacity', async () => {
    const row = {
      ...latest('d1', 't1'), battery_mv: 5612, sd_free_pct: 8, temperature_c: -2,
      health_state: 'fault', health_reasons: ['self_test_bit_9', 'sd_low'],
    }
    const { db, queries } = fakeDb(() => ({ data: [row], error: null }))
    expect(await fetchLatestTelemetry(db, ['d1'])).toEqual({
      d1: {
        lastSeen: 't1', batteryMv: 5612, batteryPct: 80, sdFreePct: 8, temperatureC: -2,
        health: 'fault', healthReasons: ['self_test_bit_9', 'sd_low'],
      },
    })
    expect(JSON.stringify(queries)).not.toContain('sd_card_used_capacity')
  })

  it('reads an unknown health_state as none', async () => {
    const { db } = fakeDb(() => ({ data: [{ ...latest('d1', 't1'), health_state: 'odd' }], error: null }))
    expect((await fetchLatestTelemetry(db, ['d1'])).d1.health).toBeNull()
  })

  it('sends the ids in chunks', async () => {
    const ids = Array.from({ length: ID_CHUNK + 1 }, (_, i) => `d${i}`)
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchLatestTelemetry(db, ids)
    expect(queries.map(q => (q.find(c => c[0] === 'in')?.[2] as string[]).length)).toEqual([ID_CHUNK, 1])
  })

  it('throws a refused read instead of showing every camera as awaiting', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'permission denied' } }))
    await expect(fetchLatestTelemetry(db, ['d1'])).rejects.toThrow('permission denied')
  })
})

describe('isLorawanCamera', () => {
  const heard = { lastSeen: '2026-11-27T10:00:00+00:00' }
  it('is a camera deployed with LoRaWAN on, or one that sent a message', () => {
    expect(isLorawanCamera({ lorawan_ping_minutes: 240 }, undefined)).toBe(true)
    expect(isLorawanCamera({ lorawan_ping_minutes: null }, heard)).toBe(true)
    expect(isLorawanCamera({ lorawan_ping_minutes: 0 }, heard)).toBe(true)
  })

  it('leaves out a camera with LoRaWAN off or no period recorded that never sent a message', () => {
    expect(isLorawanCamera({ lorawan_ping_minutes: null }, undefined)).toBe(false)
    expect(isLorawanCamera({ lorawan_ping_minutes: 0 }, undefined)).toBe(false)
    expect(isLorawanCamera({ lorawan_ping_minutes: null }, NO_MESSAGE)).toBe(false)
  })
})

describe('hasLorawanCameras', () => {
  const now = new Date(2026, 10, 27, 15, 0)
  const today = `deployment_end.is.null,deployment_end.gte.${startOfLocalDay(now).toISOString()}`

  it('asks for one deployment with a ping period and one message, both shown today', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await hasLorawanCameras(db, ['p1', 'p2'], now)).toBe(false)
    expect(queries).toEqual([
      [
        ['from', 'deployments'], ['select', 'id'], ['is', 'deleted_at', null], ['in', 'project_id', ['p1', 'p2']],
        ['gt', 'lorawan_ping_minutes', 0], ['or', today], ['limit', 1],
      ],
      [
        ['from', 'lorawan_messages'], ['select', 'id, deployments!inner(project_id, deployment_end, deleted_at)'],
        ['in', 'deployments.project_id', ['p1', 'p2']], ['is', 'deployments.deleted_at', null],
        ['or', today, { referencedTable: 'deployments' }], ['limit', 1],
      ],
    ])
  })

  it('is true when either read finds one, and counts a refused read as none', async () => {
    const one = { data: [{ id: 'x' }], error: null }
    const none = { data: [], error: null }
    const refused = { data: null, error: { message: 'permission denied' } }
    expect(await hasLorawanCameras(fakeDb((_, n) => (n === 0 ? one : none)).db, ['p1'], now)).toBe(true)
    expect(await hasLorawanCameras(fakeDb((_, n) => (n === 1 ? one : none)).db, ['p1'], now)).toBe(true)
    expect(await hasLorawanCameras(fakeDb((_, n) => (n === 0 ? refused : one)).db, ['p1'], now)).toBe(true)
    expect(await hasLorawanCameras(fakeDb(() => refused).db, ['p1'], now)).toBe(false)
  })

  it('asks nothing for no projects', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await hasLorawanCameras(db, [], now)).toBe(false)
    expect(queries).toEqual([])
  })
})

describe('fetchFieldDeployments', () => {
  it('pages past 1,000 rows with a stable order and names the project and device', async () => {
    const row = (i: number) => ({ id: `d${i}`, project_id: 'p1', projects: { name: 'P' }, devices: null, ble: null, himax: null })
    const { db, queries } = fakeDb((_, n) => n === 0
      ? { data: Array.from({ length: PAGE_SIZE }, (_, i) => row(i)), error: null }
      : { data: [row(PAGE_SIZE)], error: null })
    const deps = await fetchFieldDeployments(db, ['p1'], new Date('2026-10-21T00:00:00Z'))
    expect(deps).toHaveLength(PAGE_SIZE + 1)
    expect(deps[0]).toEqual({
      id: 'd0', project_id: 'p1', project_name: 'P', device_name: '-', no_dev_eui: false, ble_firmware: null,
      himax_firmware: null,
    })
    expect(queries[0]).toContainEqual(['order', 'id'])
    expect(queries[1]).toContainEqual(['range', PAGE_SIZE, 2 * PAGE_SIZE - 1])
  })

  it('reads the ping period and the firmware versions through their own keys', async () => {
    const row = {
      id: 'd1', project_id: 'p1', lorawan_ping_minutes: 240, projects: { name: 'P' },
      devices: { name: 'Cam', device_eui: '70B3D57ED0012345' }, ble: { version: '0.30.62' }, himax: { version: '1.4.2' },
    }
    const { db, queries } = fakeDb(() => ({ data: [row], error: null }))
    expect(await fetchFieldDeployments(db, ['p1'])).toEqual([{
      id: 'd1', project_id: 'p1', lorawan_ping_minutes: 240, project_name: 'P', device_name: 'Cam', no_dev_eui: false,
      ble_firmware: '0.30.62', himax_firmware: '1.4.2',
    }])
    const select = String(queries[0].find(c => c[0] === 'select')?.[1])
    expect(select).toContain('lorawan_ping_minutes')
    expect(select).toContain('ble:firmware!deployments_ble_firmware_id_fkey(version)')
    expect(select).toContain('himax:firmware!deployments_himax_firmware_id_fkey(version)')
  })

  it('marks a camera on record with no DevEUI, never one it cannot read (#525)', async () => {
    const base = { id: 'd1', project_id: 'p1', projects: { name: 'P' }, ble: null, himax: null }
    const rows = [
      { ...base, id: 'none', devices: { name: 'Cam', device_eui: null } },
      { ...base, id: 'blank', devices: { name: 'Cam', device_eui: ' ' } },
      { ...base, id: 'set', devices: { name: 'Cam', device_eui: '70B3D57ED0012345' } },
      { ...base, id: 'unreadable', devices: null },
    ]
    const { db, queries } = fakeDb(() => ({ data: rows, error: null }))
    const deps = await fetchFieldDeployments(db, ['p1'])
    expect(deps.map(d => [d.id, d.no_dev_eui])).toEqual([['none', true], ['blank', true], ['set', false], ['unreadable', false]])
    expect(String(queries[0].find(c => c[0] === 'select')?.[1])).toContain('devices(name, device_eui)')
  })

  it('keeps a deployment that ended today, from midnight in the viewer\'s zone', async () => {
    const now = new Date(2026, 10, 26, 15, 30)
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    await fetchFieldDeployments(db, ['p1'], now)
    const midnight = new Date(2026, 10, 26).toISOString()
    expect(queries[0]).toContainEqual(['or', `deployment_end.is.null,deployment_end.gte.${midnight}`])
  })

  it('sends no request for no projects', async () => {
    const { db, queries } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchFieldDeployments(db, [])).toEqual([])
    expect(queries).toHaveLength(0)
  })
})

describe('startOfLocalDay', () => {
  it('is local midnight of the same day', () => {
    expect(startOfLocalDay(new Date(2026, 10, 26, 23, 59, 59))).toEqual(new Date(2026, 10, 26))
    expect(startOfLocalDay(new Date(2026, 10, 26))).toEqual(new Date(2026, 10, 26))
  })
})

describe('sumDetections', () => {
  it("adds each deployment's nn_detections and skips none and zero", () => {
    expect(sumDetections([
      detected('d1', 2), detected('d1', 1, null), detected('d2', 0), detected('d3', null), detected('d4', 5),
    ])).toEqual({ d1: 3, d4: 5 })
  })
})

describe('fetchLorawanDetections', () => {
  it("reads the last 48 hours' messages with a detection and sums them per deployment", async () => {
    const now = Date.parse('2026-11-26T12:00:00Z')
    const { db, queries } = fakeDb(() => ({ data: [detected('d1', 2), detected('d1', 3), detected('d2', 1)], error: null }))
    expect(await fetchLorawanDetections(db, ['d1', 'd2'], now)).toEqual({ d1: 5, d2: 1 })
    const since = new Date(now - DETECTION_HOURS * 3_600_000).toISOString()
    expect(since).toBe('2026-11-24T12:00:00.000Z')
    expect(queries[0]).toEqual([
      ['from', 'lorawan_messages'],
      ['select', 'id, deployment_id, lorawan_parsed_messages!inner(nn_detections)'],
      ['in', 'deployment_id', ['d1', 'd2']],
      ['gte', 'received_at', since],
      ['gt', 'lorawan_parsed_messages.nn_detections', 0],
      ['order', 'id'],
      ['range', 0, PAGE_SIZE - 1],
    ])
  })

  it('pages past 1,000 messages', async () => {
    const { db, queries } = fakeDb((_, n) => n === 0
      ? { data: Array.from({ length: PAGE_SIZE }, () => detected('d1', 1)), error: null }
      : { data: [detected('d1', 1)], error: null })
    expect(await fetchLorawanDetections(db, ['d1'])).toEqual({ d1: PAGE_SIZE + 1 })
    expect(queries).toHaveLength(2)
  })

  it('throws a refused read', async () => {
    const { db } = fakeDb(() => ({ data: null, error: { message: 'permission denied' } }))
    await expect(fetchLorawanDetections(db, ['d1'])).rejects.toThrow('permission denied')
  })
})

describe('fetchDeploymentUpdate', () => {
  it("reads one deployment's telemetry and detections, two requests", async () => {
    const { db, queries } = fakeDb(calls => calls[0][1] === 'deployment_latest_lorawan'
      ? { data: [latest('d1', 't9', 55)], error: null }
      : { data: [detected('d1', 2)], error: null })
    expect(await fetchDeploymentUpdate(db, 'd1', Date.parse('2026-10-21T00:00:00Z'))).toEqual({
      telemetry: seen('t9', 55),
      detections: 2,
    })
    expect(queries).toHaveLength(2)
    expect(queries.map(q => q.find(c => c[0] === 'in'))).toEqual([
      ['in', 'deployment_id', ['d1']], ['in', 'deployment_id', ['d1']],
    ])
  })

  it('gives undefined for what the deployment does not have', async () => {
    const { db } = fakeDb(() => ({ data: [], error: null }))
    expect(await fetchDeploymentUpdate(db, 'd1')).toEqual({ telemetry: undefined, detections: undefined })
  })
})

describe('withEntry', () => {
  it('replaces one entry and leaves the map it was given alone', () => {
    const map = { d1: 1, d2: 2 }
    expect(withEntry(map, 'd1', 5)).toEqual({ d1: 5, d2: 2 })
    expect(withEntry(map, 'd3', 3)).toEqual({ d1: 1, d2: 2, d3: 3 })
    expect(withEntry(map, 'd2', undefined)).toEqual({ d1: 1 })
    expect(map).toEqual({ d1: 1, d2: 2 })
  })
})

describe('withLastSeen', () => {
  const map = { d1: { ...seen('2026-11-26T10:00:00+00:00'), health: 'fault' as const, healthReasons: ['self_test_bit_9'] } }

  it('moves the last message time forward and keeps the values', () => {
    expect(withLastSeen(map, 'd1', '2026-11-26T11:00:00.123456+00:00')).toEqual({
      d1: { ...map.d1, lastSeen: '2026-11-26T11:00:00.123456+00:00' },
    })
    expect(map.d1.lastSeen).toBe('2026-11-26T10:00:00+00:00')
  })

  it('ignores an older or unreadable time', () => {
    expect(withLastSeen(map, 'd1', '2026-11-26T09:00:00+00:00')).toBe(map)
    expect(withLastSeen(map, 'd1', 'not a time')).toBe(map)
  })

  it("gives a camera's first message an entry", () => {
    expect(withLastSeen({}, 'd2', '2026-11-26T11:00:00+00:00')).toEqual({
      d2: { ...NO_MESSAGE, lastSeen: '2026-11-26T11:00:00+00:00' },
    })
  })
})

describe('the Cameras table values', () => {
  it('shows the battery in volts, the percentage from older firmware, and a dash for neither', () => {
    expect(batteryText({ ...NO_MESSAGE, batteryMv: 5612, batteryPct: 80 })).toBe('5.6 V')
    expect(batteryText({ ...NO_MESSAGE, batteryPct: 87 })).toBe('87%')
    expect(batteryText(NO_MESSAGE)).toBe('-')
  })

  it('shows the SD card free space', () => {
    expect(sdFreeText({ ...NO_MESSAGE, sdFreePct: 41 })).toBe('41%')
    expect(sdFreeText(NO_MESSAGE)).toBe('-')
  })

  it('shows the temperature, below zero too, and a dash for none', () => {
    expect(temperatureText({ temperatureC: 16 })).toBe('16 °C')
    expect(temperatureText({ temperatureC: -2 })).toBe('-2 °C')
    expect(temperatureText({ temperatureC: 0 })).toBe('0 °C')
    expect(temperatureText(NO_MESSAGE)).toBe('-')
  })

  it('names the firmware the deployment recorded', () => {
    expect(firmwareText({ ble_firmware: '0.30.62', himax_firmware: '1.4.2' })).toBe('BLE 0.30.62 · Himax 1.4.2')
    expect(firmwareText({ ble_firmware: '0.30.62', himax_firmware: null })).toBe('BLE 0.30.62')
    expect(firmwareText({ ble_firmware: null, himax_firmware: '1.4.2' })).toBe('Himax 1.4.2')
    expect(firmwareText({ ble_firmware: null, himax_firmware: null })).toBe('-')
  })
})
