// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useLiveView: one WW500 live view connection over Web Serial (#400). The browser asks the user
// for the port (or Reconnect reopens the one used before); this opens it at 921600 baud, feeds
// each received line to a LiveViewSession (lib/liveViewSession.ts) and types what it asks for,
// one character every 30 ms. The page shows a snapshot of the session ten times a second.
// Leaving the page disconnects properly, and coming back shows that finishing; a reload or a
// closed tab cannot, so the browser asks first. Reconnect follows the cable out and back in.
import { useCallback, useEffect, useRef, useState } from 'react'
import { BAUD_RATE, LineSplitter, typeSlowly, type MotionOp } from '../lib/liveView'
import { LiveViewSession, logText, type LiveViewSnapshot } from '../lib/liveViewSession'
import { downloadBlob, logFileName } from '../lib/liveViewFiles'

// The parts of the Web Serial API used here (no DOM typings ship for it yet).
export interface PortInfo { usbVendorId?: number; usbProductId?: number }
interface SerialPortLike {
  open(options: { baudRate: number; bufferSize?: number }): Promise<void>
  close(): Promise<void>
  getInfo?(): PortInfo
  readonly readable: ReadableStream<Uint8Array> | null
  readonly writable: WritableStream<Uint8Array> | null
}
interface SerialLike {
  requestPort(): Promise<SerialPortLike>
  getPorts(): Promise<SerialPortLike[]>
  addEventListener?(type: 'connect' | 'disconnect', listener: (event: Event) => void): void
  removeEventListener?(type: 'connect' | 'disconnect', listener: (event: Event) => void): void
}
type HasInfo = { getInfo?(): PortInfo }

function webSerial(): SerialLike | null {
  return typeof navigator !== 'undefined' ? (navigator as Navigator & { serial?: SerialLike }).serial ?? null : null
}

/** Web Serial is in desktop Chrome and Edge, not in Firefox or Safari. */
export function serialSupported(): boolean {
  return webSerial() !== null
}

/** A frame line is up to about 70 KB; the default 255-byte read buffer overruns at 92 KB/s. */
const READ_BUFFER = 1 << 20
const POLL_MS = 50
const PUBLISH_MS = 100
const PORT_KEY = 'ww.liveView.port.v1'
const PORT_BUSY = 'Could not open that port. Close any other program using the cable (live_view.py, '
  + 'TeraTerm, the ship-check app), then connect again.'

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))
const now = () => Date.now()

/** The port this page used last, while the page is open; after a reload, the granted port with
 *  the same USB ids, when only one has them (a bench often has two identical FTDI cables). */
let lastPort: SerialPortLike | null = null
/** The session of a page that was left: it finishes Disconnect before the port closes. */
let running: { session: LiveViewSession; done: Promise<void> } | null = null

function sameIds(port: HasInfo, want: PortInfo): boolean {
  const info = port.getInfo?.() ?? {}
  return info.usbVendorId === want.usbVendorId && info.usbProductId === want.usbProductId
}

/** The port Reconnect opens: the last one while it is granted, else the only one with its ids. */
export function pickReconnectPort<P extends HasInfo>(ports: readonly P[], last: P | null, want: PortInfo): P | null {
  if (last && ports.includes(last)) return last
  const same = ports.filter(p => sameIds(p, want))
  return same.length === 1 ? same[0] : null
}

/** The last port after `plugged` came in: a cable with the same ids back in place of one that went. */
export function portAfterPlugIn<P extends HasInfo>(plugged: P, ports: readonly P[], last: P | null, want: PortInfo): P | null {
  return (!last || !ports.includes(last)) && sameIds(plugged, want) ? plugged : last
}

function rememberedIds(): PortInfo {
  try {
    return JSON.parse(localStorage.getItem(PORT_KEY) ?? '{}') as PortInfo
  } catch {
    return {} // storage off or unreadable: no remembered port
  }
}

async function reconnectablePort(serial: SerialLike): Promise<SerialPortLike | null> {
  return pickReconnectPort(await serial.getPorts(), lastPort, rememberedIds())
}

function rememberPort(port: SerialPortLike): void {
  lastPort = port
  try {
    localStorage.setItem(PORT_KEY, JSON.stringify(port.getInfo?.() ?? {}))
  } catch { /* storage off: Reconnect still works until the page is reloaded */ }
}

async function run(port: SerialPortLike, session: LiveViewSession): Promise<void> {
  const io: { reader: ReadableStreamDefaultReader<Uint8Array> | null } = { reader: null }
  const reading = (async () => {
    const splitter = new LineSplitter()
    const decoder = new TextDecoder()
    // A framing or overrun error ends one reader but not the port: take a new one.
    while (port.readable && !session.stopped) {
      const reader = port.readable.getReader()
      io.reader = reader
      try {
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          for (const line of splitter.push(value)) session.handleLine(decoder.decode(line), Date.now())
        }
      } catch (err) {
        session.note(Date.now(), `read error: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        reader.releaseLock()
      }
    }
    session.disconnected(Date.now())
  })()

  const writer = port.writable?.getWriter()
  try {
    while (writer && !session.stopped) {
      const now = Date.now()
      session.tick(now)
      const text = session.takeCommand(now)
      if (text === null) {
        await sleep(POLL_MS)
        continue
      }
      await typeSlowly(text, byte => writer.write(byte), sleep)
      session.typed(text, Date.now())
    }
  } catch (err) {
    session.note(Date.now(), `write error: ${err instanceof Error ? err.message : String(err)}`)
  } finally {
    session.disconnected(Date.now())
    writer?.releaseLock()
    await io.reader?.cancel().catch(() => undefined)
    await reading
    await port.close().catch(() => undefined)
  }
}

export function useLiveView() {
  const [view, setView] = useState<LiveViewSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [canReconnect, setCanReconnect] = useState(false)
  const sessionRef = useRef<LiveViewSession | null>(null)
  // The last session, kept after Disconnect so its log can still be saved.
  const lastSessionRef = useRef<LiveViewSession | null>(null)
  // Stops showing the session when the page goes; the session itself carries on.
  const unfollowRef = useRef<(() => void) | null>(null)

  const checkReconnect = useCallback(async () => {
    const serial = webSerial()
    setCanReconnect(!!serial && (await reconnectablePort(serial).catch(() => null)) !== null)
  }, [])

  /** Shows a session ten times a second until it ends, then offers Reconnect. */
  const follow = useCallback((session: LiveViewSession, done: Promise<void>) => {
    sessionRef.current = session
    lastSessionRef.current = session
    let shown = -1
    let following = true
    const publish = () => {
      if (session.version === shown) return
      shown = session.version
      setView(session.snapshot())
    }
    const timer = window.setInterval(publish, PUBLISH_MS)
    unfollowRef.current = () => {
      following = false
      window.clearInterval(timer)
    }
    const ended = () => {
      if (!following) return
      window.clearInterval(timer)
      publish()
      if (sessionRef.current === session) sessionRef.current = null
      void checkReconnect()
    }
    done.then(ended, ended)
  }, [checkReconnect])

  // Offer Reconnect on arrival when this browser already granted the camera's port, and again when
  // a granted cable goes or comes back.
  useEffect(() => {
    const serial = webSerial()
    if (!serial) return
    let alive = true
    const check = () => {
      reconnectablePort(serial).then(port => { if (alive) setCanReconnect(port !== null) }, () => undefined)
    }
    // The event's target is the port plugged in.
    const pluggedIn = (event: Event) => {
      const plugged = event.target as unknown as SerialPortLike | null
      serial.getPorts().then(ports => {
        if (plugged) lastPort = portAfterPlugIn(plugged, ports, lastPort, rememberedIds())
        check()
      }, () => undefined)
    }
    check()
    serial.addEventListener?.('connect', pluggedIn)
    serial.addEventListener?.('disconnect', check)
    return () => {
      alive = false
      serial.removeEventListener?.('connect', pluggedIn)
      serial.removeEventListener?.('disconnect', check)
    }
  }, [])

  // Leaving the page: the session carries on until the camera is asleep, then closes the port.
  // Coming back meanwhile shows it finishing, rather than a port that will not open.
  useEffect(() => {
    if (running && !running.session.stopped) follow(running.session, running.done)
    return () => {
      sessionRef.current?.disconnect(Date.now())
      unfollowRef.current?.()
    }
  }, [follow])

  const active = view !== null && view.phase !== 'stopped'
  useEffect(() => {
    if (!active) return
    const ask = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', ask)
    return () => window.removeEventListener('beforeunload', ask)
  }, [active])

  const open = useCallback(async (port: SerialPortLike) => {
    try {
      await port.open({ baudRate: BAUD_RATE, bufferSize: READ_BUFFER })
    } catch {
      setError(PORT_BUSY)
      return
    }
    rememberPort(port)
    const session = new LiveViewSession()
    session.start(Date.now())
    const done = run(port, session).finally(() => {
      if (running?.session === session) running = null
    })
    running = { session, done }
    follow(session, done)
  }, [follow])

  const connect = useCallback(async () => {
    const serial = webSerial()
    if (!serial || sessionRef.current) return
    setError(null)
    let port: SerialPortLike
    try {
      port = await serial.requestPort()
    } catch {
      return // the user closed the port chooser
    }
    await open(port)
  }, [open])

  const reconnect = useCallback(async () => {
    const serial = webSerial()
    if (!serial || sessionRef.current) return
    setError(null)
    const port = await reconnectablePort(serial).catch(() => null)
    if (port) await open(port)
    else void connect()
  }, [open, connect])

  const disconnect = useCallback(() => sessionRef.current?.disconnect(now()), [])
  const freeRun = useCallback(() => sessionRef.current?.freeRun(now()), [])
  const pause = useCallback(() => sessionRef.current?.pause(now()), [])
  const oneImage = useCallback(() => sessionRef.current?.oneImage(now()), [])
  const setOp = useCallback((op: MotionOp, value: number) => sessionRef.current?.setOp(op, value, now()), [])
  const setFrameInterval = useCallback((ms: number) => sessionRef.current?.setFrameInterval(ms, now()), [])
  const setMotionDetection = useCallback((on: boolean) => sessionRef.current?.setMotionDetection(on, now()), [])
  const setKeepAwake = useCallback((on: boolean) => sessionRef.current?.setKeepAwake(on, now()), [])
  const sendCommand = useCallback((command: string) => sessionRef.current?.sendCommand(command, now()), [])
  const saveLog = useCallback(() => {
    const session = lastSessionRef.current
    if (session) downloadBlob(new Blob([logText(session.fullLog()), '\n'], { type: 'text/plain' }), logFileName(new Date()))
  }, [])

  return {
    view, error, canReconnect, connect, reconnect, disconnect, freeRun, pause, oneImage, setOp,
    setFrameInterval, setMotionDetection, setKeepAwake, sendCommand, saveLog,
  }
}
