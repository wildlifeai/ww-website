// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// useLiveView: one WW500 live view connection over Web Serial (#400). The browser asks the user
// for the port; this opens it at 921600 baud, feeds each received line to a LiveViewSession
// (lib/liveViewSession.ts) and types what it asks for, one character every 30 ms. The page shows
// a snapshot of the session ten times a second. Leaving the page stops the camera properly; a
// reload or a closed tab cannot, so the browser asks first.
import { useCallback, useEffect, useRef, useState } from 'react'
import { BAUD_RATE, LineSplitter, typeSlowly, type MotionOp } from '../lib/liveView'
import { LiveViewSession, type LiveViewSnapshot } from '../lib/liveViewSession'

// The parts of the Web Serial API used here (no DOM typings ship for it yet).
interface SerialPortLike {
  open(options: { baudRate: number; bufferSize?: number }): Promise<void>
  close(): Promise<void>
  readonly readable: ReadableStream<Uint8Array> | null
  readonly writable: WritableStream<Uint8Array> | null
}
interface SerialLike {
  requestPort(): Promise<SerialPortLike>
}

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
const PORT_BUSY = 'Could not open that port. Close any other program using the cable (live_view.py, '
  + 'TeraTerm, the ship-check app), then connect again.'

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

async function run(port: SerialPortLike, session: LiveViewSession, publish: () => void): Promise<void> {
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
      publish()
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
  const sessionRef = useRef<LiveViewSession | null>(null)

  // Leaving the page: the session carries on until the camera has stopped, then closes the port.
  useEffect(() => () => sessionRef.current?.stop(Date.now()), [])

  const active = view !== null && view.phase !== 'stopped'
  useEffect(() => {
    if (!active) return
    const ask = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', ask)
    return () => window.removeEventListener('beforeunload', ask)
  }, [active])

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
    try {
      await port.open({ baudRate: BAUD_RATE, bufferSize: READ_BUFFER })
    } catch {
      setError(PORT_BUSY)
      return
    }
    const session = new LiveViewSession()
    sessionRef.current = session
    let shown = -1
    const publish = () => {
      if (session.version === shown) return
      shown = session.version
      setView(session.snapshot())
    }
    session.start(Date.now())
    publish()
    const timer = window.setInterval(publish, PUBLISH_MS)
    await run(port, session, publish)
    window.clearInterval(timer)
    publish()
    sessionRef.current = null
  }, [])

  const stop = useCallback(() => sessionRef.current?.stop(Date.now()), [])
  const setOp = useCallback((op: MotionOp, value: number) => sessionRef.current?.setOp(op, value, Date.now()), [])

  return { view, error, connect, stop, setOp }
}
