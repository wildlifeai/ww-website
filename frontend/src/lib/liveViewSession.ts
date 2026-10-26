/**
 * One live view connection to a WW500 (#400): what to type and when, driven by the console
 * lines that come back. A port of the sequence in the firmware's `_Tools/live_view.py`
 * (Seeed dev f3ae0faf), with its controls; `_Documentation/live_preview.md` is the contract.
 *
 * Pure: the caller passes the time, feeds it each console line, and types whatever
 * `takeCommand()` returns (useLiveView does that over Web Serial).
 *
 * - Connect: `ver`, read ops 7 and 8 (to put them back), keep the camera awake (`setop 8 60000`),
 *   read the camera type and ops 10, 11, 17 and 33, then `preview 1` and Free run: `capture 10
 *   <ms>`, again each time a burst ends.
 * - Run modes: Free run re-arms after every burst; Stop lets the burst finish and arms no more
 *   (preview stays on, so nothing is saved to the card); One image takes a single frame after the
 *   current burst. A wake from sleep starts Free run again, as live_view.py does.
 * - After a cold boot the firmware refuses captures until it has slept once, so the page sets a
 *   10 s timelapse wake (`setop 7 10`) and sends it to sleep (`dpd`); it streams when it wakes.
 *   That wake's own burst starts before `preview 1` can be typed, so it saves one photo.
 * - Each step is typed when the console is free: while a burst streams, right after a frame or
 *   once nothing has arrived for 3 s; with no burst running, as soon as the step before it is
 *   answered. A step is typed again until its reply shows, up to 8 times; a query once; a
 *   command from the command box once, without waiting for an answer.
 * - Disconnect waits for the burst to end, counting a `capture` already typed but not yet
 *   acknowledged (frames after `preview 0` would be saved to the SD card), then `preview 0`, puts
 *   ops 7 and 8 back as found, and `dpd`.
 */
import {
  DEVICE_LINES, LINES, OP_MD_INTERVAL, OP_SENSITIVITY, OP_THRESHOLD, captureIntervalMs, cleanLine,
  motionCells, parseFrameLine, replyExpected, validFrameInterval, validOpValue, wouldWake, type MotionOp,
  type PreviewFrame,
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
/** Lines the log pane shows; the whole session goes into a saved log. */
const LOG_TAIL = 300
/** A bound on the session log, about an hour of frames at 20 ms. */
const MAX_LOG_LINES = 250000
const OP_CAMERA_ENABLED = 10
/** The op params the page shows: timelapse and inactivity, camera enabled, and the motion ops. */
const TRACKED_OPS = [7, 8, OP_CAMERA_ENABLED, OP_MD_INTERVAL, OP_SENSITIVITY, OP_THRESHOLD]
const STATUS_QUERIES = ['camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33']

interface Step {
  cmd: string | (() => string)
  expect: string[]
  tries: number
  /** A query: typed once, and no note when it goes unanswered. */
  query: boolean
}

export type LiveViewPhase = 'starting' | 'sleeping' | 'streaming' | 'paused' | 'stopping' | 'stopped'
/** Free run re-arms after every burst; paused arms nothing; one takes a single frame. */
export type RunMode = 'free' | 'paused' | 'one'

export interface LiveViewFrame {
  count: number
  width: number
  height: number
  /** base64 JPEG */
  image: string
  /** The JPEG's size in bytes. */
  bytes: number
  /** Cells with motion in picture order (motionCells), or null: the frame carries no motion grid. */
  cells: number[] | null
  /** MD_BLOCK_NUM_TH of context A (the preview frames) and B (the frames it checks asleep). */
  threshold: number | null
  thresholdB: number | null
  wake: boolean | null
}

export interface LogLine {
  at: number
  text: string
}

/** What the camera said about itself: the boot banner, `ver`, the SD card. */
export interface DeviceInfo {
  board: string | null
  built: string | null
  branch: string | null
  version: string | null
  camera: string | null
  boot: 'cold' | 'warm' | null
  wakeReason: string | null
  flashOffset: string | null
  sd: 'ready' | 'failed' | null
  nextImage: number | null
  imagesDir: string | null
}

export interface LiveViewSnapshot {
  phase: LiveViewPhase
  /** Why it is asleep: the first sleep after a cold boot, or Keep awake off. */
  sleepReason: 'first-sleep' | 'idle' | null
  /** Nothing heard from the camera since connecting: it is probably asleep. */
  silent: boolean
  /** The connection ended without Disconnect (cable out, port lost). */
  lost: boolean
  runMode: RunMode
  motionDetection: boolean
  keepAwake: boolean
  /** The interval of the next frame, and whether it was set in the box rather than derived. */
  intervalMs: number
  intervalSet: boolean
  camera: string | null
  /** Ops 7, 8, 10, 11, 17 and 33 as last reported, or as just set. */
  ops: Partial<Record<number, number>>
  frame: LiveViewFrame | null
  /** Frames with a motion grid, and those that would wake the camera, since Free run. */
  md: { frames: number; wakes: number }
  device: DeviceInfo
  dropped: number
  /** Disconnect steps the camera never confirmed. */
  unconfirmed: string[]
  /** The latest lines, for the log pane; fullLog() has the session. */
  log: LogLine[]
  logLines: number
}

const verified = (cmd: string | (() => string), expect: string): Step =>
  ({ cmd, expect: [expect], tries: SEND_TRIES, query: false })
const PREVIEW_ON = 'preview 1'
const isCapture = (s: Step) => s.expect[0] === 'About to capture'
const text = (s: Step) => (typeof s.cmd === 'function' ? s.cmd() : s.cmd)

const NO_DEVICE: DeviceInfo = {
  board: null, built: null, branch: null, version: null, camera: null, boot: null, wakeReason: null,
  flashOffset: null, sd: null, nextImage: null, imagesDir: null,
}

function kb(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`
}

export class LiveViewSession {
  version = 0
  stopped = false
  private lost = false
  private log: LogLine[] = []
  private logTrimmed = false
  private camera: string | null = null
  private ops: Partial<Record<number, number>> = {}
  private op11: number | null = null
  private runMode: RunMode = 'free'
  private oneShotPending = false
  private mdOn = true
  private keepAwake = true
  private userIntervalMs: number | null = null
  private intervalMs = captureIntervalMs(null, null)
  private frame: LiveViewFrame | null = null
  private mdFrames = 0
  private wakeFrames = 0
  private device: DeviceInfo = { ...NO_DEVICE }
  private dropped = 0
  private unconfirmed: string[] = []
  private heard = false
  private silent = false

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
  private previewOn = false
  private coldBoot = false
  private dpdPending = false
  /** Asleep on its own (Keep awake off): the next boot is a wake. */
  private asleep = false
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
      this.onFrame(frame, now)
      return
    }
    this.push(now, line)
    this.confirm(line)
    this.readDevice(line)

    const op = LINES.op.exec(line)
    if (op) this.onOp(Number(op[1]), Number(op[2]), now)
    const cam = LINES.camera.exec(line)
    if (cam && (cam[1] ?? cam[2]) !== this.camera) {
      this.camera = cam[1] ?? cam[2]
      this.intervalChanged(now)
    }
    if (LINES.previewOn.test(line)) this.previewOn = true
    if (LINES.previewOff.test(line)) this.previewOn = false
    const saved = LINES.sdWrite.exec(line)
    if (saved && !this.previewOn) {
      this.note(now, `the camera saved ${saved[1]} to its SD card: a wake's own burst starts before preview can be switched on`)
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
    if (LINES.enteringDpd.test(line)) {
      this.captureBusy = false
      this.previewOn = false
      if (!this.dpdPending && !this.coldBoot) {
        this.asleep = true
        this.note(now, 'the camera went to sleep: motion or its timer wakes it, and Free run starts again')
      }
    }
    const cold = LINES.coldBoot.test(line)
    if (cold || LINES.warmBoot.test(line)) {
      this.dpdPending = false
      this.asleep = false
      this.coldBoot = cold
      this.captureBusy = false
      this.previewOn = false
      this.sleepAt = null
      this.device.boot = cold ? 'cold' : 'warm'
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

  /** Timers: the deferred sleep and arm, the re-arm if a burst end was missed, the disconnect. */
  tick(now: number): void {
    if (this.stopped) return
    if (this.sleepAt !== null && now >= this.sleepAt) this.sleepNow(now)
    if (this.armAt !== null && now >= this.armAt) {
      this.armAt = null
      if (!this.stopping) this.arm(now, 'camera ready')
    }
    const quietFor = now - Math.max(this.lastFrameAt, this.busyAt, this.captureSentAt)
    if (!this.stopping && !this.coldBoot && !this.asleep && this.runMode === 'free' && this.lastFrameAt > -Infinity
      && now - this.lastFrameAt > REARM_QUIET_MS && now - this.lastRearmAt > REARM_QUIET_MS && !this.seq.length) {
      this.note(now, 'no frames for 20 s: asking again')
      this.lastRearmAt = now
      this.captureBusy = false
      this.arm(now, 'stream quiet', false)
    }
    // Disconnect waits for the burst; a capture that was typed but never acknowledged does not start one.
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
   * soon as the step before is answered. With nothing queued and Keep awake on, a bare CR every
   * 20 s keeps the console's 60 s inactivity period from running out, except while it must sleep.
   */
  takeCommand(now: number): string | null {
    if (this.stopped) return null
    const force = this.window
    this.window = false
    if (!this.seq.length) {
      const keepAlive = this.keepAwake && !this.coldBoot && !this.dpdPending && !this.asleep && this.sleepAt === null
        && !this.stopping && now - this.lastTxAt >= KEEPALIVE_MS && now - this.lastFrameAt >= QUIET_MS
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
    // answered must not hold the next one back for the retry period. A command that expects no
    // answer is done once typed.
    const head = this.inFlight && this.seq[0] === this.inFlight ? this.inFlight : null
    if (head && !head.expect.length) {
      this.seq.shift()
      this.seqLastSend = -Infinity
    } else if (head) {
      this.seqLastSend = now
    }
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

  /** A command from the command box or a quick-command button: typed once, when the console is free. */
  sendCommand(command: string, now: number): void {
    const cmd = command.trim()
    if (!cmd || this.stopping || this.stopped) return
    this.query(cmd)
    this.note(now, `command: ${cmd}`)
  }

  /** Bursts again and again; the wake counts start over. */
  freeRun(now: number): void {
    if (this.stopping) return
    this.runMode = 'free'
    this.oneShotPending = false
    this.mdFrames = 0
    this.wakeFrames = 0
    this.note(now, 'free run')
    if (!this.capturing(now)) this.arm(now, 'free run', !this.previewOn)
  }

  /** Arms no more bursts; the one running finishes, with preview still on. */
  pause(now: number): void {
    if (this.stopping) return
    this.runMode = 'paused'
    this.oneShotPending = false
    this.seq = this.seq.filter(s => !isCapture(s))
    this.note(now, this.capturing(now) ? 'stop: after the current burst' : 'stop')
  }

  /** A single frame, after the burst that is running. */
  oneImage(now: number): void {
    if (this.stopping) return
    this.runMode = 'one'
    this.oneShotPending = true
    this.seq = this.seq.filter(s => !isCapture(s))
    this.note(now, this.capturing(now) ? 'one image: after the current burst' : 'one image')
    if (!this.capturing(now)) this.arm(now, 'one image', !this.previewOn)
  }

  /** The interval of the frames from the next one; it holds until Motion detection is switched. */
  setFrameInterval(ms: number, now: number): void {
    if (this.stopping || !validFrameInterval(ms)) return
    this.userIntervalMs = ms
    this.intervalChanged(now)
  }

  /** Motion detection on: the grid and the wake counts, frames at op 11's interval on the HM0360.
   *  Off: plain live video at 20 ms (100 ms on the RP3). */
  setMotionDetection(on: boolean, now: number): void {
    if (on === this.mdOn) return
    this.mdOn = on
    this.userIntervalMs = null
    this.mdFrames = 0
    this.wakeFrames = 0
    this.note(now, `motion detection ${on ? 'on' : 'off'}`)
    this.intervalChanged(now)
  }

  /** Keep awake off lets the camera sleep after op 8's inactivity period, to test a wake. */
  setKeepAwake(on: boolean, now: number): void {
    if (on === this.keepAwake) return
    this.keepAwake = on
    this.note(now, on ? 'keep awake on' : 'keep awake off: the camera sleeps once it has been idle for op 8')
  }

  /** Preview off, ops 7 and 8 back as found, then sleep, after the burst that is running. */
  disconnect(now: number): void {
    if (this.stopping) return
    this.stopping = true
    this.armAt = null
    this.sleepAt = null
    this.seq = this.seq.filter(s => !isCapture(s) && text(s) !== PREVIEW_ON)
    if (this.capturing(now) && now - Math.max(this.lastFrameAt, this.busyAt, this.captureSentAt) <= REARM_QUIET_MS) {
      this.note(now, 'disconnecting after the current burst')
    } else {
      this.close(now)
    }
  }

  /** The port closed or failed without Disconnect. */
  disconnected(now: number): void {
    if (this.stopped) return
    this.lost = true
    this.finish(now)
  }

  snapshot(): LiveViewSnapshot {
    const phase = this.phase()
    return {
      phase,
      sleepReason: phase !== 'sleeping' ? null : this.asleep ? 'idle' : 'first-sleep',
      silent: this.silent && !this.heard,
      lost: this.lost,
      runMode: this.runMode,
      motionDetection: this.mdOn,
      keepAwake: this.keepAwake,
      intervalMs: this.intervalMs,
      intervalSet: this.userIntervalMs !== null,
      camera: this.camera,
      ops: { ...this.ops },
      frame: this.frame,
      md: { frames: this.mdFrames, wakes: this.wakeFrames },
      device: { ...this.device },
      dropped: this.dropped,
      unconfirmed: [...this.unconfirmed],
      log: this.log.slice(-LOG_TAIL),
      logLines: this.log.length,
    }
  }

  /** Every line of the session, for a saved log. */
  fullLog(): readonly LogLine[] {
    return this.log
  }

  note(now: number, message: string): void {
    this.push(now, `[page] ${message}`)
  }

  /** A burst is running, or a `capture` was typed and may still start one. */
  private capturing(now: number): boolean {
    return this.captureBusy || now - this.captureSentAt <= CAPTURE_ACK_MS
  }

  private phase(): LiveViewPhase {
    if (this.stopped) return 'stopped'
    if (this.stopping) return 'stopping'
    if (this.coldBoot || this.dpdPending || this.asleep) return 'sleeping'
    if (this.runMode !== 'free' && !this.captureBusy && !this.oneShotPending && this.frame) return 'paused'
    return this.frame ? 'streaming' : 'starting'
  }

  private onFrame(frame: PreviewFrame, now: number): void {
    this.lastFrameAt = now
    this.captureBusy = true
    this.window = true
    const cells = frame.md ? motionCells(frame.md) : null
    const threshold = frame.mdstat?.thA ?? this.ops[OP_THRESHOLD] ?? null
    const thresholdB = frame.mdstat?.thB ?? null
    const wake = cells && threshold !== null ? wouldWake(cells.length, threshold) : null
    const bytes = Math.floor((frame.image.length * 3) / 4) - (frame.image.endsWith('==') ? 2 : frame.image.endsWith('=') ? 1 : 0)
    if (!this.frame) this.note(now, `first frame, ${frame.width}x${frame.height}, ${cells ? 'with' : 'without'} a motion grid`)
    this.frame = { count: frame.count, width: frame.width, height: frame.height, image: frame.image, bytes, cells, threshold, thresholdB, wake }
    if (this.mdOn && cells) {
      this.mdFrames++
      if (wake) this.wakeFrames++
    }
    const md = cells
      ? `, motion ${cells.length}, threshold ${threshold ?? '-'} (B ${thresholdB ?? '-'}), ${wake ? 'WAKE' : 'no wake'}`
      : ', no motion grid'
    this.push(now, `frame ${frame.count}, ${frame.width}x${frame.height}, ${kb(bytes)}${md}`)
  }

  private push(now: number, line: string): void {
    this.log.push({ at: now, text: line.length > 300 ? `${line.slice(0, 300)}...` : line })
    if (this.log.length > MAX_LOG_LINES) {
      this.log.splice(0, this.log.length - Math.floor(MAX_LOG_LINES * 0.9))
      if (!this.logTrimmed) {
        this.logTrimmed = true
        this.log.unshift({ at: now, text: `[page] the log keeps the latest ${MAX_LOG_LINES.toLocaleString('en-NZ')} lines` })
      }
    }
    this.version++
  }

  private query(cmd: string): void {
    this.seq.push({ cmd, expect: replyExpected(cmd), tries: 1, query: true })
  }

  private confirm(line: string): void {
    const head = this.seq[0]
    if (head && head.expect.some(m => line.includes(m))) {
      if (text(head) === 'ver') this.device.version = line.replace(/^\*+\s*|\s*\*+$/g, '')
      this.seq.shift()
      this.seqLastSend = -Infinity
    }
  }

  /** The Device tab's details, from the boot banner and the SD card lines. */
  private readDevice(line: string): void {
    const d = this.device
    const build = DEVICE_LINES.build.exec(line)
    if (build) [d.board, d.built] = [build[1], build[2]]
    const branch = DEVICE_LINES.branch.exec(line)
    if (branch) d.branch = `${branch[1]} ${branch[2]}`
    const camera = DEVICE_LINES.camera.exec(line)
    if (camera) d.camera = camera[1]
    const wake = DEVICE_LINES.wakeReason.exec(line)
    if (wake) d.wakeReason = wake[1]
    const slot = DEVICE_LINES.flashOffset.exec(line)
    if (slot) d.flashOffset = slot[1]
    const next = DEVICE_LINES.nextImage.exec(line)
    if (next) d.nextImage = Number(next[1])
    const dir = DEVICE_LINES.imagesDir.exec(line)
    if (dir) d.imagesDir = dir[1]
    if (DEVICE_LINES.sdReady.test(line)) d.sd = 'ready'
    if (DEVICE_LINES.sdFailed.test(line)) d.sd = 'failed'
    if (LINES.sdWrite.test(line) && d.nextImage !== null) d.nextImage++
  }

  /** Reads ops 7 and 8 before the page first writes them, to put them back at Disconnect. */
  private readOriginals(): void {
    for (const op of [7, 8]) {
      const cmd = `getop ${op}`
      if (this.original[op] === undefined && !this.written.has(op) && !this.seq.some(s => s.cmd === cmd)) this.query(cmd)
    }
  }

  private onOp(op: number, value: number, now: number): void {
    if ((op === 7 || op === 8) && this.original[op] === undefined && !this.written.has(op)) this.original[op] = value
    if (TRACKED_OPS.includes(op)) this.ops[op] = value
    if (op === OP_MD_INTERVAL && value !== this.op11) {
      this.op11 = value
      this.intervalChanged(now)
    }
  }

  /** The stream's interval: the box's value, else the camera type, op 11 and the MD switch. */
  private intervalChanged(now: number): void {
    const ms = this.userIntervalMs ?? captureIntervalMs(this.camera, this.op11, this.mdOn)
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
      // would otherwise be saved to the SD card. Free run starts again after a wake.
      this.note(now, 'the camera woke: preview on, Free run once it is ready')
      this.runMode = 'free'
      this.oneShotPending = false
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

  /** Queues the next capture: a burst in Free run, one frame for One image, nothing when stopped. */
  private arm(now: number, reason: string, withPreview = true): void {
    if (this.coldBoot) {
      this.note(now, `${reason}: no capture yet, the camera has to sleep once first`)
      return
    }
    if (this.runMode === 'paused' || (this.runMode === 'one' && !this.oneShotPending)) return
    if (this.seq.some(isCapture)) return
    if (this.captureBusy && now - Math.max(this.lastFrameAt, this.busyAt) > REARM_QUIET_MS) this.captureBusy = false
    if (this.captureBusy) return
    const frames = this.runMode === 'one' ? 1 : BURST_FRAMES
    if (this.runMode === 'one') this.oneShotPending = false
    const capture = verified(() => `capture ${frames} ${this.intervalMs}`, 'About to capture')
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
   * Preview off, ops 7 and 8 back as found, then sleep. A value the page itself set (a 10 s wake,
   * 60 s awake) means an earlier session ended without Disconnect: then the quiet values
   * live_view.py leaves (no timelapse, 1 s) go back instead.
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
    this.note(now, 'disconnecting: preview off, sleep settings back, then sleep')
  }

  private finish(now: number): void {
    if (this.closing) this.unconfirmed.push(...this.seq.map(text))
    this.seq = []
    this.stopped = true
    this.note(now, this.lost ? 'the connection closed'
      : this.unconfirmed.length ? `disconnected, not confirmed: ${this.unconfirmed.join(', ')}` : 'disconnected: the camera is asleep')
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
      return view.sleepReason === 'idle'
        ? 'The camera is asleep. Motion or its timer wakes it, and Free run starts again.'
        : `The camera sleeps once after it starts, then wakes in about ${WAKE_SECONDS} s.`
    case 'streaming':
      return view.runMode === 'free' ? null : 'Finishing the current burst…'
    case 'paused':
      return 'Stopped. Free run or One image takes more pictures.'
    case 'stopping':
      return 'Disconnecting after the current pictures…'
    case 'stopped':
      if (view.lost) return 'The connection closed. Reconnect and press Disconnect, so the camera sleeps normally.'
      return view.unconfirmed.length
        ? `Disconnected, but the camera did not confirm: ${view.unconfirmed.join(', ')}. Reconnect and disconnect to finish.`
        : 'Disconnected. The camera is asleep with its sleep settings put back.'
  }
}

/** The console log as text, each line with its time. */
export function logText(log: readonly LogLine[]): string {
  return log.map(l => {
    const t = new Date(l.at)
    const hms = [t.getHours(), t.getMinutes(), t.getSeconds()].map(n => String(n).padStart(2, '0')).join(':')
    return `${hms}.${String(t.getMilliseconds()).padStart(3, '0')} ${l.text}`
  }).join('\n')
}
