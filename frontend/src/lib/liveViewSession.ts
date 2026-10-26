/**
 * One live view connection to a WW500 (#400): what to type and when, driven by the console
 * lines that come back. A port of the sequence in the firmware's `_Tools/live_view.py`
 * (e3c4d18a), without its manual controls; `_Documentation/live_preview.md` is the contract.
 *
 * Pure: the caller passes the time, feeds it each console line, and types whatever
 * `takeCommand()` returns (useLiveView does that over Web Serial).
 *
 * - Connect: `ver`, read ops 7 and 8 (to put them back), keep the camera awake (`setop 8 60000`),
 *   read the camera type and ops 10, 11, 17 and 33, then `preview 1` and `capture 10 <ms>`, again
 *   each time a burst ends.
 * - After a cold boot the firmware refuses captures until it has slept once, so the page sets a
 *   10 s timelapse wake (`setop 7 10`) and sends it to sleep (`dpd`); it streams when it wakes.
 * - Each step is typed when the console is free: while a burst streams, right after a frame or
 *   once nothing has arrived for 3 s; with no burst running, as soon as the step before it is
 *   answered. A step is typed again until its reply shows, up to 8 times; a query once.
 * - Stop waits for the burst to end, counting a `capture` already typed but not yet acknowledged
 *   (frames after `preview 0` would be saved to the SD card), then `preview 0`, puts ops 7 and 8
 *   back, and `dpd`.
 */
import {
  LINES, OP_MD_INTERVAL, OP_SENSITIVITY, OP_THRESHOLD, captureIntervalMs, cleanLine, motionCells,
  parseFrameLine, replyExpected, validOpValue, wouldWake, type MotionOp,
} from './liveView'

export const WAKE_SECONDS = 10
export const BURST_FRAMES = 10
const KEEP_AWAKE_MS = 60000
/** The short inactivity period live_view.py leaves on close, when op 8 was not read. */
const OP8_QUIET_MS = 1000
const SEND_RETRY_MS = 3000
/** A typed `capture` counts as running until acknowledged, or this long without an answer
 *  (the bench answered in 0.7 s, 26 Oct 2026). */
const CAPTURE_ACK_MS = 4000
const SEND_TRIES = 8
const QUIET_MS = 3000
const REARM_QUIET_MS = 20000
const KEEPALIVE_MS = 20000
const CLI_UP_GAP_MS = 10000
const BOOT_SETTLE_MS = 4000
const BOOT_FALLBACK_MS = 10000
const SILENT_MS = 10000
const CLOSE_TIMEOUT_MS = 30000
const LOG_LINES = 300
const OP_CAMERA_ENABLED = 10
const STATUS_QUERIES = ['camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33']

interface Step {
  cmd: string | (() => string)
  expect: string[]
  tries: number
  /** A query: typed once, and no note when it goes unanswered. */
  query: boolean
}

export type LiveViewPhase = 'starting' | 'sleeping' | 'streaming' | 'stopping' | 'stopped'

export interface LiveViewFrame {
  count: number
  width: number
  height: number
  /** base64 JPEG */
  image: string
  /** null: the frame carries no motion grid */
  cells: number[] | null
  threshold: number | null
  wake: boolean | null
}

export interface LogLine {
  at: number
  text: string
}

export interface LiveViewSnapshot {
  phase: LiveViewPhase
  /** Nothing heard from the camera since connecting: it is probably asleep. */
  silent: boolean
  /** The connection ended without Stop (cable out, port lost). */
  lost: boolean
  camera: string | null
  /** Ops 10, 11, 17 and 33 as last reported, or as just set. */
  ops: Partial<Record<number, number>>
  frame: LiveViewFrame | null
  dropped: number
  /** Stop steps the camera never confirmed. */
  unconfirmed: string[]
  log: LogLine[]
}

const verified = (cmd: string | (() => string), expect: string): Step =>
  ({ cmd, expect: [expect], tries: SEND_TRIES, query: false })
const PREVIEW_ON = 'preview 1'
const isCapture = (s: Step) => s.expect[0] === 'About to capture'
const text = (s: Step) => (typeof s.cmd === 'function' ? s.cmd() : s.cmd)

export class LiveViewSession {
  version = 0
  stopped = false
  private lost = false
  private log: LogLine[] = []
  private camera: string | null = null
  private ops: Partial<Record<number, number>> = {}
  private op11: number | null = null
  private intervalMs = captureIntervalMs(null, null)
  private frame: LiveViewFrame | null = null
  private dropped = 0
  private unconfirmed: string[] = []
  private heard = false
  private silent = false
  private lastWake: boolean | null = null

  private seq: Step[] = []
  private seqLastSend = -Infinity
  /** The step being typed: its answer can arrive before the last character's pause is over. */
  private inFlight: Step | null = null
  private captureSentAt = -Infinity
  /** A frame or a burst just ended: the console is free to type into. */
  private window = false
  private startedAt = 0
  private lastFrameAt = -Infinity
  private lastTxAt = -Infinity
  private lastCliUpAt = -Infinity
  private lastRearmAt = -Infinity
  private busyAt = -Infinity
  private captureBusy = false
  private coldBoot = false
  private dpdPending = false
  private armAt: number | null = null
  private sleepAt: number | null = null
  private wakeSet = false
  private stopping = false
  private closing = false
  private closeBy = 0
  private readonly original: Partial<Record<number, number>> = {}
  private readonly written = new Set<number>()

  start(now: number): void {
    this.startedAt = now
    this.note(now, 'connected: asking the camera for its version')
    this.seq.push(verified('ver', 'WW500'))
    this.readOriginals()
    this.seq.push(verified(`setop 8 ${KEEP_AWAKE_MS}`, `Set OpParam 8 = ${KEEP_AWAKE_MS}`))
    STATUS_QUERIES.forEach(q => this.query(q))
    this.arm(now, 'connected')
  }

  /** One console line, as received. */
  handleLine(raw: string, now: number): void {
    const line = cleanLine(raw)
    if (!line) return
    this.heard = true
    this.version++
    const frame = parseFrameLine(line)
    if (frame === 'damaged') {
      this.dropped++
      this.note(now, `dropped a damaged frame (${this.dropped} so far)`)
      return
    }
    if (frame) {
      this.lastFrameAt = now
      this.captureBusy = true
      this.window = true
      const cells = frame.md ? motionCells(frame.md) : null
      const threshold = frame.mdstat?.thA ?? this.ops[OP_THRESHOLD] ?? null
      const wake = cells && threshold !== null ? wouldWake(cells.length, threshold) : null
      if (!this.frame) this.note(now, `first frame, ${frame.width}x${frame.height}, ${cells ? 'with' : 'without'} a motion grid`)
      this.frame = { count: frame.count, width: frame.width, height: frame.height, image: frame.image, cells, threshold, wake }
      if (wake !== null && wake !== this.lastWake) {
        this.note(now, `frame ${frame.count}: ${cells?.length} cells, threshold ${threshold}: ${wake ? 'WAKE' : 'no wake'}`)
      }
      this.lastWake = wake
      return
    }
    this.push(now, line)
    this.confirm(line)

    const op = LINES.op.exec(line)
    if (op) this.onOp(Number(op[1]), Number(op[2]), now)
    const cam = LINES.camera.exec(line)
    if (cam && (cam[1] ?? cam[2]) !== this.camera) {
      this.camera = cam[1] ?? cam[2]
      this.intervalChanged(now)
    }
    if (LINES.captureOk.test(line) || LINES.burstStart.test(line)) {
      this.captureBusy = true
      this.busyAt = now
      this.coldBoot = false
    }
    if (LINES.burstEnd.test(line)) this.burstEnded(now)
    if (LINES.refused.test(line)) {
      // Connected during the cold-boot period: it has to sleep first. We have typed at it, which
      // keeps it awake 60 s, so send it to sleep now.
      this.coldBoot = true
      this.seq = this.seq.filter(s => !isCapture(s))
      this.sleepNow(now)
      return
    }
    const cold = LINES.coldBoot.test(line)
    if (cold || LINES.warmBoot.test(line)) {
      this.dpdPending = false
      this.coldBoot = cold
      this.captureBusy = false
      this.sleepAt = null
    }
    if (this.stopping) return
    if (LINES.cliUp.test(line) && now - this.lastFrameAt > CLI_UP_GAP_MS && now - this.lastCliUpAt > CLI_UP_GAP_MS) {
      this.booted(now)
    }
    if (LINES.bootDone.test(line)) {
      if (!this.coldBoot) this.armAt = now + BOOT_SETTLE_MS
      else if (this.sleepAt !== null) this.sleepAt = now + BOOT_SETTLE_MS
    }
  }

  /** Timers: the deferred sleep and arm, the re-arm if a burst end was missed, the stop. */
  tick(now: number): void {
    if (this.stopped) return
    if (this.sleepAt !== null && now >= this.sleepAt) this.sleepNow(now)
    if (this.armAt !== null && now >= this.armAt) {
      this.armAt = null
      if (!this.stopping) this.arm(now, 'camera ready')
    }
    const quietFor = now - Math.max(this.lastFrameAt, this.busyAt, this.captureSentAt)
    if (!this.stopping && !this.coldBoot && this.lastFrameAt > -Infinity && now - this.lastFrameAt > REARM_QUIET_MS
      && now - this.lastRearmAt > REARM_QUIET_MS && !this.seq.length) {
      this.note(now, 'no frames for 20 s: asking again')
      this.lastRearmAt = now
      this.captureBusy = false
      this.arm(now, 'stream quiet', false)
    }
    // Stop waits for the burst; a capture that was typed but never acknowledged does not start one.
    if (this.stopping && !this.closing && (quietFor > REARM_QUIET_MS || !this.capturing(now))) this.close(now)
    if (this.closing && (!this.seq.length || now > this.closeBy)) this.finish(now)
    if (!this.heard && !this.silent && now - this.startedAt > SILENT_MS) {
      this.silent = true
      this.note(now, 'no answer yet: the camera is probably asleep')
    }
  }

  /**
   * The text to type now, or null. While a burst streams, steps go right after a frame or a burst
   * end, or at the retry pace once the stream has been quiet for 3 s; with no burst running, as
   * soon as the step before is answered. With nothing queued, a bare CR every 20 s keeps the
   * console's 60 s inactivity period from running out, except while the camera must sleep.
   */
  takeCommand(now: number): string | null {
    if (this.stopped) return null
    const force = this.window
    this.window = false
    if (!this.seq.length) {
      const keepAlive = !this.coldBoot && !this.dpdPending && this.sleepAt === null && !this.stopping
        && now - this.lastTxAt >= KEEPALIVE_MS && now - this.lastFrameAt >= QUIET_MS
      return keepAlive ? '\r' : null
    }
    const framesFlowing = this.capturing(now) && now - this.lastFrameAt <= QUIET_MS
    if (!force && (framesFlowing || now - this.seqLastSend < SEND_RETRY_MS)) return null
    while (this.seq.length) {
      const step = this.seq[0]
      if (step.tries > 0) {
        step.tries--
        this.inFlight = step
        if (isCapture(step)) this.captureSentAt = now
        return `${text(step)}\r\n`
      }
      this.seq.shift()
      if (!step.query) {
        this.note(now, `no answer to "${text(step)}", giving up`)
        if (this.closing) this.unconfirmed.push(text(step))
      }
    }
    return null
  }

  /** Call when `takeCommand()`'s text has been typed. */
  typed(typedText: string, now: number): void {
    this.lastTxAt = now
    const cmd = typedText.trim()
    if (!cmd) return
    // The camera often answers inside the last character's pause, before this call: a step already
    // answered must not hold the next one back for the retry period.
    if (this.inFlight && this.seq[0] === this.inFlight) this.seqLastSend = now
    if (this.inFlight && isCapture(this.inFlight)) this.captureSentAt = now
    this.inFlight = null
    this.push(now, `> ${cmd}`)
    const setop = /^setop (\d+) /.exec(cmd)
    if (setop) this.written.add(Number(setop[1]))
    if (cmd === 'dpd') this.dpdPending = true
  }

  /** Writes a motion op and reads it back, so a value the firmware refuses does not stay shown. */
  setOp(op: MotionOp, value: number, now: number): void {
    if (this.stopping || !validOpValue(op, value)) return
    this.ops[op] = value
    this.query(`setop ${op} ${value}`)
    this.query(`getop ${op}`)
    this.note(now, `setting op ${op} to ${value}`)
  }

  stop(now: number): void {
    if (this.stopping) return
    this.stopping = true
    this.armAt = null
    this.sleepAt = null
    this.seq = this.seq.filter(s => !isCapture(s) && text(s) !== PREVIEW_ON)
    if (this.capturing(now) && now - Math.max(this.lastFrameAt, this.busyAt, this.captureSentAt) <= REARM_QUIET_MS) {
      this.note(now, 'stopping after the current burst')
    } else {
      this.close(now)
    }
  }

  /** A burst is running, or a `capture` was typed and may still start one. */
  private capturing(now: number): boolean {
    return this.captureBusy || now - this.captureSentAt <= CAPTURE_ACK_MS
  }

  /** The port closed or failed without Stop. */
  disconnected(now: number): void {
    if (this.stopped) return
    this.lost = true
    this.finish(now)
  }

  snapshot(): LiveViewSnapshot {
    return {
      phase: this.phase(),
      silent: this.silent && !this.heard,
      lost: this.lost,
      camera: this.camera,
      ops: { ...this.ops },
      frame: this.frame,
      dropped: this.dropped,
      unconfirmed: [...this.unconfirmed],
      log: [...this.log],
    }
  }

  note(now: number, message: string): void {
    this.push(now, `[page] ${message}`)
  }

  private phase(): LiveViewPhase {
    if (this.stopped) return 'stopped'
    if (this.stopping) return 'stopping'
    if (this.coldBoot || this.dpdPending) return 'sleeping'
    return this.frame ? 'streaming' : 'starting'
  }

  private push(now: number, line: string): void {
    this.log.push({ at: now, text: line.length > 300 ? `${line.slice(0, 300)}...` : line })
    if (this.log.length > LOG_LINES) this.log.splice(0, this.log.length - LOG_LINES)
    this.version++
  }

  private query(cmd: string): void {
    this.seq.push({ cmd, expect: replyExpected(cmd), tries: 1, query: true })
  }

  private confirm(line: string): void {
    const head = this.seq[0]
    if (head && head.expect.some(m => line.includes(m))) {
      this.seq.shift()
      this.seqLastSend = -Infinity
    }
  }

  /** Reads ops 7 and 8 before the page first writes them, to put them back at Stop. */
  private readOriginals(): void {
    for (const op of [7, 8]) {
      const cmd = `getop ${op}`
      if (this.original[op] === undefined && !this.written.has(op) && !this.seq.some(s => s.cmd === cmd)) this.query(cmd)
    }
  }

  private onOp(op: number, value: number, now: number): void {
    if ((op === 7 || op === 8) && this.original[op] === undefined && !this.written.has(op)) this.original[op] = value
    if ([OP_CAMERA_ENABLED, OP_MD_INTERVAL, OP_SENSITIVITY, OP_THRESHOLD].includes(op)) this.ops[op] = value
    if (op === OP_MD_INTERVAL && value !== this.op11) {
      this.op11 = value
      this.intervalChanged(now)
    }
  }

  /** The stream's interval follows the camera type and op 11, from the next frame. */
  private intervalChanged(now: number): void {
    const ms = captureIntervalMs(this.camera, this.op11)
    if (ms === this.intervalMs) return
    this.intervalMs = ms
    this.note(now, `${this.camera ?? 'camera'}: a frame every ${ms} ms`)
    if (this.captureBusy && !this.coldBoot && !this.stopping) this.query(`interval ${ms}`)
  }

  /** The CLI is up after a boot: drop what was queued for the camera before it slept. */
  private booted(now: number): void {
    this.lastCliUpAt = now
    this.armAt = null
    this.seq = []
    if (this.coldBoot) {
      this.note(now, `cold boot: it takes no pictures until it has slept once, so it sleeps after starting and wakes in ${WAKE_SECONDS} s`)
      this.readOriginals()
      this.setWake()
      this.sleepAt = now + BOOT_FALLBACK_MS
    } else {
      // Preview first: preview is cleared by every sleep, and a wake starts its own burst, which
      // would otherwise be saved to the SD card.
      this.note(now, 'the camera woke: preview on, capturing once it is ready')
      this.seq.push(verified(PREVIEW_ON, 'Preview mode 1'))
      this.readOriginals()
      this.seq.push(verified(`setop 8 ${KEEP_AWAKE_MS}`, `Set OpParam 8 = ${KEEP_AWAKE_MS}`))
      STATUS_QUERIES.forEach(q => this.query(q))
      this.armAt = now + BOOT_FALLBACK_MS
    }
  }

  private setWake(): void {
    if (this.wakeSet) return
    this.wakeSet = true
    this.seq.push(verified(`setop 7 ${WAKE_SECONDS}`, `Set OpParam 7 = ${WAKE_SECONDS}`))
  }

  private sleepNow(now: number): void {
    this.sleepAt = null
    this.readOriginals()
    this.setWake()
    this.note(now, `sending the camera to sleep: it wakes in ${WAKE_SECONDS} s`)
    this.seq.push(verified('dpd', 'Forcing DPD'))
  }

  private arm(now: number, reason: string, withPreview = true): void {
    if (this.coldBoot) {
      this.note(now, `${reason}: no capture yet, the camera has to sleep once first`)
      return
    }
    if (this.seq.some(isCapture)) return
    if (this.captureBusy && now - Math.max(this.lastFrameAt, this.busyAt) > REARM_QUIET_MS) this.captureBusy = false
    if (this.captureBusy) return
    const capture = verified(() => `capture ${BURST_FRAMES} ${this.intervalMs}`, 'About to capture')
    this.seq.push(...(withPreview ? [verified(PREVIEW_ON, 'Preview mode 1'), capture] : [capture]))
  }

  private burstEnded(now: number): void {
    this.captureBusy = false
    this.window = true
    if (this.stopping) {
      if (!this.closing) this.close(now)
    } else {
      this.arm(now, 'burst finished', false)
    }
  }

  /**
   * Preview off, ops 7 and 8 back, then sleep. A value the page itself set (a 10 s wake, 60 s
   * awake) means an earlier session ended without Stop: then the quiet values live_view.py
   * leaves (no timelapse, 1 s) go back instead.
   */
  private close(now: number): void {
    this.closing = true
    this.closeBy = now + CLOSE_TIMEOUT_MS
    this.seq = [verified('preview 0', 'Preview off')]
    if (this.written.has(7) || this.original[7] === WAKE_SECONDS) {
      const op7 = this.original[7] === undefined || this.original[7] === WAKE_SECONDS ? 0 : this.original[7]
      this.seq.push(verified(`setop 7 ${op7}`, `Set OpParam 7 = ${op7}`))
    }
    if (this.written.has(8)) {
      const op8 = this.original[8] === undefined || this.original[8] === KEEP_AWAKE_MS ? OP8_QUIET_MS : this.original[8]
      this.seq.push(verified(`setop 8 ${op8}`, `Set OpParam 8 = ${op8}`))
    }
    this.seq.push(verified('dpd', 'Forcing DPD'))
    this.window = true
    this.note(now, 'stopping: preview off, sleep settings back, then sleep')
  }

  private finish(now: number): void {
    if (this.closing) this.unconfirmed.push(...this.seq.map(text))
    this.seq = []
    this.stopped = true
    this.note(now, this.lost ? 'the connection closed'
      : this.unconfirmed.length ? `stopped, not confirmed: ${this.unconfirmed.join(', ')}` : 'stopped: the camera is asleep')
  }
}

/** The one line under the Connect button, or null when the picture says it all. */
export function liveViewStatus(view: LiveViewSnapshot | null): string | null {
  if (!view) return null
  switch (view.phase) {
    case 'starting':
      return view.silent
        ? 'No answer from the camera: it is probably asleep. Reset it, or switch it off and on.'
        : 'Waiting for the camera…'
    case 'sleeping':
      return `The camera sleeps once after it starts, then wakes in about ${WAKE_SECONDS} s.`
    case 'streaming':
      return null
    case 'stopping':
      return 'Stopping after the current pictures…'
    case 'stopped':
      if (view.lost) return 'The connection closed. Connect again and press Stop, so the camera sleeps normally.'
      return view.unconfirmed.length
        ? `Stopped, but the camera did not confirm: ${view.unconfirmed.join(', ')}. Connect again and stop to finish.`
        : 'Stopped. The camera is asleep with its sleep settings put back.'
  }
}

/** The console log as text, each line with its time. */
export function logText(log: LogLine[]): string {
  return log.map(l => {
    const t = new Date(l.at)
    const hms = [t.getHours(), t.getMinutes(), t.getSeconds()].map(n => String(n).padStart(2, '0')).join(':')
    return `${hms}.${String(t.getMilliseconds()).padStart(3, '0')} ${l.text}`
  }).join('\n')
}
