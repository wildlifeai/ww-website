import { describe, expect, it } from 'vitest'
import { pickReconnectPort, portAfterPlugIn, type PortInfo } from './useLiveView'

const FTDI: PortInfo = { usbVendorId: 0x0403, usbProductId: 0x6001 }
const port = (info: PortInfo = FTDI) => ({ getInfo: () => info })

describe('Reconnect after the cable is pulled out and plugged back in (#528)', () => {
  it('offers no port while the cable is out, and the one that comes back', () => {
    const used = port()
    expect(pickReconnectPort([used], used, FTDI)).toBe(used)
    expect(pickReconnectPort([], used, FTDI)).toBeNull()
    const back = port()
    expect(pickReconnectPort([back], portAfterPlugIn(back, [back], used, FTDI), FTDI)).toBe(back)
  })

  it('with two identical cables, the one plugged back in, not the other', () => {
    // The bench's two TTL232R-3V3 cables share their USB ids, so the ids alone pick neither.
    const used = port()
    const other = port()
    const back = port()
    expect(pickReconnectPort([other, back], used, FTDI)).toBeNull()
    const last = portAfterPlugIn(back, [other, back], used, FTDI)
    expect(pickReconnectPort([other, back], last, FTDI)).toBe(back)
  })

  it('keeps the port in use when another cable is plugged in', () => {
    const used = port()
    const other = port()
    expect(portAfterPlugIn(other, [used, other], used, FTDI)).toBe(used)
    expect(portAfterPlugIn(port({ usbVendorId: 0x10c4, usbProductId: 0xea60 }), [], null, FTDI)).toBeNull()
  })
})
