/**
 * One live view connection to a WW500 (#400): what to type and when, driven by the console
 * lines that come back. A port of the sequence in the firmware's `_Tools/live_view.py`
 * (Seeed dev f3ae0faf), with its controls; `_Documentation/live_preview.md` is the contract.
 *
 * Pure: the caller passes the time, feeds it each console line, and types whatever
 * `takeCommand()` returns (useLiveView does that over Web Serial).
 *
 * Each step follows a line from the camera, never a wait (#528):
 * - Connect: `ver`, and nothing else until it answers, as a sleeping camera hears nothing and the
 *   Bluetooth chip's port prints lines that are not an answer. Then read ops 7 and 8 (to put them
 *   back), keep the camera awake (`setop 8 60000`), read the camera type, ops 10, 11, 17 and 33 and
 *   the SD card, then `preview 1` and Free run: `capture 10 <ms>`, again each time a burst ends.
 * - A boot is `### Cold Boot ###` or `### Warm Boot ###`, nothing else. Typing waits for its command
 *   line (`Starting CLI Task`), and a write for the camera and the card (`Image sensor and data path
 *   initialised`, `Inactivity period set at`): CONFIG.TXT, loaded in between, overwrites an op.
 * - After a cold boot the firmware refuses captures until it has slept once, so the page sets a
 *   10 s timelapse wake (`setop 7 10`) and sends it to sleep (`dpd`); it streams when it wakes, and
 *   op 7 goes back as found. That wake's own burst starts before `preview 1` can be typed, so it
 *   saves one photo. Firmware that does not know `preview` takes no pictures from the page.
 * - Run modes: Free run re-arms after every burst; Stop lets the burst finish and arms no more
 *   (preview stays on, so nothing is saved to the card); One image takes a single frame after the
 *   current burst. A wake starts Free run again, as live_view.py does, unless Keep awake is off:
 *   unticking it stops Free run, so the camera can sleep, and each wake shows its own burst.
 * - Each step is typed when the console is free: while a burst streams, right after a frame or
 *   once nothing has arrived for 3 s; with no burst running, as soon as the step before it is
 *   answered. A capture waits for a burst that is running. A step is typed again until its reply
 *   shows, up to 8 times; a query once; a command from the command box once, without waiting for
 *   an answer. "Command not recognised" answers the step whose echo came just before it.
 * - Disconnect waits for the burst to end, counting a `capture` already typed but not yet
 *   acknowledged (frames after `preview 0` would be saved to the SD card), then `preview 0`, puts
 *   ops 7 and 8 back as found, and `dpd`. From a camera that never answered it closes at once.
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
/** Shown only: this long after Connect without an answer, the page says why. */
const SILENT_MS = 10000
/** Shown only: frames stopped this long, or two frame intervals, reads as no picture. */
const NO_PICTURE_MS = 5000
const CLOSE_TIMEOUT_MS = 30000
/** Lines the log pane shows; the whole session goes into a saved log. */
const LOG_TAIL = 300
/** A bound on the session log, about an hour of frames at 20 ms. */
const MAX_LOG_LINES = 250000
const OP_CAMERA_ENABLED = 10
/** The op params the page shows: timelapse and inactivity, camera enabled, and the motion ops. */
const TRACKED_OPS = [7, 8, OP_CAMERA_ENABLED, OP_MD_INTERVAL, OP_SENSITIVITY, OP_THRESHOLD]
const STATUS_QUERIES = ['camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33', 'sdused']
const OP_NAMES: Record<number, string> = {
  [OP_SENSITIVITY]: 'the sensitivity', [OP_THRESHOLD]: 'the wake threshold', [OP_MD_INTERVAL]: 'the MD interval',
}

interface Step {
  cmd: string | (() => string)
  expect: string[]
  tries: number
  /** A query: typed once, and no note when it goes unanswered. */
  query: boolean
  /** A write from the controls: the op whose value shows as pending until it is answered. */
  op?: number
}

/** A boot in progress: what it has finished, as the camera reported it. */
interface Boot {
  cold: boolean
  cli: boolean
  camera: boolean
  card: boolean
}

/** The way to the first picture after Connect or a boot; gone once it shows. */
interface Startup {
  restarted: boolean
  /** It has to sleep once first: a cold boot, or a refused capture. */
  sleepOnce: boolean
  asleepAt: number | null
  woke: boolean
}

export type LiveViewPhase = 'starting' | 'sleeping' | 'streaming' | 'paused' | 'stopping' | 'stopped'
/** Free run re-arms after every burst; paused arms nothing; one takes a single frame. */
export type RunMode = 'free' | 'paused' | 'one'
/** Why there is no picture, shown over it: nothing heard, lines but no answer, no `preview`. */
export type LiveViewWarning = 'asleep' | 'other-port' | 'no-preview'
export type WakeCause = 'motion' | 'timer' | 'bluetooth'

export interface LiveViewStep {
  label: string
  state: 'done' | 'now' | 'todo'
}

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
  /** Ready, or failed: no card, or none it can read. */
  sd: 'ready' | 'failed' | null
  sdUsed: number | null
  nextImage: number | null
  imagesDir: string | null
}

export interface LiveViewSnapshot {
  phase: LiveViewPhase
  /** Why it is asleep: the first sleep after a cold boot, or Keep awake off. */
  sleepReason: 'first-sleep' | 'idle' | null
  /** Shown over the picture in place of the steps. */
  warning: LiveViewWarning | null
  /** The steps to the first picture, shown over it, or null once it is there. */
  steps: LiveViewStep[] | null
  /** Seconds since the last frame, when frames are due and have stopped. */
  noPictureFor: number | null
  /** What woke the camera last, until it sleeps again. */
  wokeBy: WakeCause | null
  /** The camera answered `ver`, or booted: this is its AI console. */
  answered: boolean
  /** The connection ended without Disconnect (cable out, port lost). */
  lost: boolean
  runMode: RunMode
  motionDetection: boolean
  keepAwake: boolean
  /** The interval of the next frame, and whether it was set in the box rather than derived. */
  intervalMs: number
  intervalSet: boolean
  camera: string | null
  /** Ops 7, 8, 10, 11, 17 and 33 as the camera last reported them. */
  ops: Partial<Record<number, number>>
  /** Motion ops written and not answered yet. */
  pendingOps: Partial<Record<number, number>>
  /** The last write the camera refused or did not answer. */
  opNotice: string | null
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
const WAKE_CAUSE: Record<string, WakeCause> = { 'Motion detected INT_INDIC': 'motion', 'Timer wake': 'timer', 'BLE wake': 'bluetooth' }
const WAKE_WORDS: Record<WakeCause, string> = { motion: 'motion', timer: 'timer', bluetooth: 'the Bluetooth chip' }

const NO_DEVICE: DeviceInfo = {
  board: null, built: null, branch: null, version: null, camera: null, boot: null, wakeReason: null,
  flashOffset: null, sd: null, sdUsed: null, nextImage: null, imagesDir: null,
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
  private pending: Partial<Record<number, number>> = {}
  private opNotice: string | null = null
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
  /** Any line at all since Connect. */
  private heard = false
  /** `ver` answered, or a boot seen: this is a WW500's AI console. */
  private answered = false
  private silenceNoted = false
  private noPreview = false
  private wokeBy: WakeCause | null = null
  /** The command the console echoed last, to tell which step "Command not recognised" answers. */
  private lastEcho = ''

  private seq: Step[] = []
  private seqLastSend = -Infinity
  /** The step being typed: its answer can arrive before the last character's pause is over. */
  private inFlight: Step | null = null
  private captureSentAt = -Infinity
  /** A frame or a burst just ended: the console is free to type into. */
  private window = false
  private startedAt = 0
  /** The latest time the caller passed, for what the snapshot counts (no-picture, the wake). */
  private clock = 0
  private shownSecond = -1
  private lastFrameAt = -Infinity
  private lastTxAt = -Infinity
  private lastRearmAt = -Infinity
  private busyAt = -Infinity
  private captureBusy = false
  /** The burst running is one the page asked for, not a wake's own. */
  private ownBurst = false
  private previewOn = false
  private coldBoot = false
  private dpdPending = false
  /** Asleep on its own (Keep awake off): the next boot is a wake. */
  private asleep = false
  private boot: Boot | null = null
  private startup: Startup | null = null
  private stopping = false
  private closing = false
  private closeBy = 0
  private readonly original: Partial<Record<number, number>> = {}
  private readonly written = new Set<number>()

  start(now: number): void {
    this.startedAt = now
    this.clock = now
    this.startup = { restarted: false, sleepOnce: false, asleepAt: null, woke: false }
    this.note(now, 'connected: asking the camera for its version')
    this.seq.push(verified('ver', 'WW500'))
  }

  /** One console line, as received. */
  handleLine(raw: string, now: number): void {
    const line = cleanLine(raw)
    if (!line) return
    this.clock = now
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
    const echo = LINES.echo.exec(line)
    if (echo) this.lastEcho = echo[1]
    this.confirm(line, now)
    this.readDevice(line)

    const op = LINES.op.exec(line)
    if (op) this.onOp(Number(op[1]), Number(op[2]), now)
    const refusedOp = LINES.opRefused.exec(line)
    if (refusedOp) this.opFailed(Number(refusedOp[2]), `The camera refused ${refusedOp[1]} for ${OP_NAMES[Number(refusedOp[2])] ?? `op ${refusedOp[2]}`}.`)
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
    if (LINES.captureOk.test(line)) {
      // Acknowledged: the burst is the page's, and the typed capture no longer counts on its own.
      this.ownBurst = true
      this.captureSentAt = -Infinity
    }
    if (LINES.burstEnd.test(line)) this.burstEnded(now)
    if (LINES.refused.test(line)) {
      // Connected during the cold-boot period: it has to sleep first. We have typed at it, which
      // keeps it awake 60 s, so send it to sleep now.
      this.coldBoot = true
      this.seq = this.seq.filter(s => !isCapture(s))
      this.startup = { ...(this.startup ?? { restarted: false, asleepAt: null, woke: false }), sleepOnce: true }
      this.sleepNow(now)
      return
    }
    if (LINES.enteringDpd.test(line)) this.wentToSleep(now)
    const cold = LINES.coldBoot.test(line)
    if (cold || LINES.warmBoot.test(line)) this.onBoot(cold, now)
    const wake = DEVICE_LINES.wake.exec(line)
    if (wake) this.wokeBy = WAKE_CAUSE[wake[1]]
    if (this.boot) this.bootProgress(line, now)
  }

  /** Timers: the re-arm if a burst end was missed, the disconnect, and what the page counts. */
  tick(now: number): void {
    if (this.stopped) return
    this.clock = now
    // The countdowns on the page move once a second.
    const second = Math.floor(now / 1000)
    if (second !== this.shownSecond) {
      this.shownSecond = second
      this.version++
    }
    const quietFor = now - Math.max(this.lastFrameAt, this.busyAt, this.captureSentAt)
    if (!this.stopping && !this.boot && !this.coldBoot && !this.asleep && this.runMode === 'free' && this.lastFrameAt > -Infinity
      && now - this.lastFrameAt > REARM_QUIET_MS && now - this.lastRearmAt > REARM_QUIET_MS && !this.seq.length) {
      this.note(now, 'no frames for 20 s: asking again')
      this.lastRearmAt = now
      this.captureBusy = false
      this.arm(now, 'stream quiet', false)
    }
    // Disconnect waits for the burst; a capture that was typed but never acknowledged does not start one.
    if (this.stopping && !this.closing && (quietFor > REARM_QUIET_MS || !this.capturing(now))) this.close(now)
    if (this.closing && (!this.seq.length || now > this.closeBy)) this.finish(now)
    if (!this.answered && !this.silenceNoted && now - this.startedAt > SILENT_MS) {
      this.silenceNoted = true
      this.note(now, this.heard
        ? 'lines, but no answer to "ver": this may be the Bluetooth chip\'s port'
        : 'no answer: the camera is asleep, press its RESET button')
    }
  }

  /**
   * The text to type now, or null. While a burst streams, steps go right after a frame or a burst
   * end, or at the retry pace once the stream has been quiet for 3 s; with no burst running, as
   * soon as the step before is answered. Nothing while a boot has no command line yet. With nothing
   * queued and Keep awake on, a bare CR every 20 s keeps the console's 60 s inactivity period from
   * running out, except while it must sleep.
   */
  takeCommand(now: number): string | null {
    if (this.stopped) return null
    // Characters typed into a boot land in front of the next command.
    if (this.boot && !this.boot.cli) return null
    const force = this.window
    this.window = false
    if (!this.seq.length) {
      const keepAlive = this.keepAwake && this.answered && !this.boot && !this.coldBoot && !this.dpdPending && !this.asleep
        && !this.stopping && now - this.lastTxAt >= KEEPALIVE_MS && now - this.lastFrameAt >= QUIET_MS
      return keepAlive ? '\r' : null
    }
    // A capture waits for the burst that is running (a wake's own, say) to end.
    if (isCapture(this.seq[0]) && this.captureBusy && now - Math.max(this.lastFrameAt, this.busyAt) <= REARM_QUIET_MS) return null
    const framesFlowing = this.capturing(now) && now - this.lastFrameAt <= QUIET_MS
    if (!force && (framesFlowing || now - this.seqLastSend < SEND_RETRY_MS)) return null
    while (this.seq.length) {
      const step = this.seq[0]
      if (step.tries > 0) {
        step.tries--
        this.inFlight = step
        this.lastEcho = ''
        if (isCapture(step)) this.captureSentAt = now
        return `${text(step)}\r\n`
      }
      this.seq.shift()
      if (step.op !== undefined) this.opFailed(step.op, `No answer to "${text(step)}" from the camera.`)
      if (!step.query) {
        this.note(now, `no answer to "${text(step)}", giving up`)
        if (this.closing) this.unconfirmed.push(text(step))
        else if (text(step) === PREVIEW_ON) this.dropCaptures(now, 'no capture without preview, or its pictures would be saved to the SD card')
      }
    }
    return null
  }

  /** Call when `takeCommand()`'s text has been typed. */
  typed(typedText: string, now: number): void {
    this.lastTxAt = now
    const cmd = typedText.trim()
    if (!cmd) return
    this.push(now, `> ${cmd}`)
    const step = this.inFlight
    this.inFlight = null
    // A boot while it was typed: it went into the boot, so it changed nothing.
    if (!step) return
    // The camera often answers inside the last character's pause, before this call: a step already
    // answered must not hold the next one back for the retry period. A command that expects no
    // answer is done once typed.
    if (this.seq[0] === step && !step.expect.length) {
      this.seq.shift()
      this.seqLastSend = -Infinity
    } else if (this.seq[0] === step) {
      this.seqLastSend = now
    }
    if (isCapture(step)) this.captureSentAt = now
    const setop = /^setop (\d+) /.exec(cmd)
    if (setop) this.written.add(Number(setop[1]))
    if (cmd === 'dpd') this.dpdPending = true
  }

  /** Writes a motion op and reads it back; it shows as pending until the camera answers. */
  setOp(op: MotionOp, value: number, now: number): void {
    if (this.stopping || !validOpValue(op, value)) return
    const cmd = `setop ${op} ${value}`
    this.pending[op] = value
    this.opNotice = null
    this.seq.push({ cmd, expect: replyExpected(cmd), tries: SEND_TRIES, query: false, op })
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
    // Awake, no picture is coming now; the first sleep still has to happen.
    if (!this.coldBoot && !this.dpdPending) this.startup = null
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

  /** The motion grid on: the grid and the wake counts, frames at op 11's interval on the HM0360.
   *  Off: plain live video at 20 ms (100 ms on the RP3). Nothing changes on the camera. */
  setMotionDetection(on: boolean, now: number): void {
    if (on === this.mdOn) return
    this.mdOn = on
    this.userIntervalMs = null
    this.mdFrames = 0
    this.wakeFrames = 0
    this.note(now, `motion grid ${on ? 'on' : 'off'}`)
    this.intervalChanged(now)
  }

  /** Keep awake off stops Free run, whose captures keep the camera awake, so it can sleep once it
   *  has been idle for a minute and show what wakes it. */
  setKeepAwake(on: boolean, now: number): void {
    if (on === this.keepAwake) return
    this.keepAwake = on
    if (!on && this.runMode === 'free') this.pause(now)
    this.note(now, on ? 'keep awake on'
      : 'keep awake off: the camera sleeps after a minute without commands, and a wake shows its own pictures')
  }

  /** Preview off, ops 7 and 8 back as found, then sleep, after the burst that is running. */
  disconnect(now: number): void {
    if (this.stopping) return
    this.stopping = true
    // Asleep, or not a WW500's AI console: nothing on it was changed, so nothing goes back.
    if (!this.answered) {
      this.finish(now)
      return
    }
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
      warning: this.warning(),
      steps: this.steps(),
      noPictureFor: this.noPictureFor(phase),
      wokeBy: this.wokeBy,
      answered: this.answered,
      lost: this.lost,
      runMode: this.runMode,
      motionDetection: this.mdOn,
      keepAwake: this.keepAwake,
      intervalMs: this.intervalMs,
      intervalSet: this.userIntervalMs !== null,
      camera: this.camera,
      ops: { ...this.ops },
      pendingOps: { ...this.pending },
      opNotice: this.opNotice,
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
    if (this.startup) return 'starting'
    if (this.runMode !== 'free' && !this.captureBusy && !this.oneShotPending) return 'paused'
    return 'streaming'
  }

  private warning(): LiveViewWarning | null {
    if (this.stopping || this.stopped) return null
    if (!this.answered && this.clock - this.startedAt > SILENT_MS) return this.heard ? 'other-port' : 'asleep'
    return this.noPreview ? 'no-preview' : null
  }

  private steps(): LiveViewStep[] | null {
    const s = this.startup
    if (!s || this.stopping || this.stopped) return null
    const list: [label: string, done: boolean][] = [
      s.restarted ? ['Camera restarts', true] : s.woke && !s.sleepOnce ? ['Camera wakes', true] : ['Camera answers', this.answered],
    ]
    if (s.sleepOnce) {
      const wakeIn = s.asleepAt === null ? null : Math.ceil((s.asleepAt + WAKE_SECONDS * 1000 - this.clock) / 1000)
      list.push(['Sleeps once first', s.asleepAt !== null || s.woke])
      list.push([
        s.woke ? 'Wakes up' : wakeIn === null ? `Wakes ${WAKE_SECONDS} s later` : wakeIn > 0 ? `Wakes in ${wakeIn} s` : 'Wakes any moment',
        s.woke,
      ])
    }
    list.push(['First picture', false])
    const current = list.findIndex(([, done]) => !done)
    return list.map(([label, done], i) => ({ label, state: done ? 'done' : i === current ? 'now' : 'todo' }))
  }

  /** Frames are due (Free run, streaming) and none has come for a while: a camera switched off. */
  private noPictureFor(phase: LiveViewPhase): number | null {
    if (phase !== 'streaming' || this.runMode !== 'free' || this.lastFrameAt === -Infinity) return null
    const gap = this.clock - this.lastFrameAt
    return gap > Math.max(NO_PICTURE_MS, 2 * this.intervalMs) ? Math.floor(gap / 1000) : null
  }

  private onFrame(frame: PreviewFrame, now: number): void {
    this.lastFrameAt = now
    this.captureBusy = true
    this.window = true
    this.startup = null
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

  private confirm(line: string, now: number): void {
    const head = this.seq[0]
    if (!head) return
    if (LINES.notRecognised.test(line)) {
      if (this.lastEcho === text(head)) this.unknownCommand(head, now)
      return
    }
    // The boot banner names the board too, but it is not an answer to `ver`.
    if (!head.expect.some(m => line.includes(m)) || (text(head) === 'ver' && DEVICE_LINES.build.test(line))) return
    this.seq.shift()
    this.seqLastSend = -Infinity
    // A refusal keeps it pending for opFailed(), which says why.
    if (head.op !== undefined && !LINES.opRefused.test(line)) delete this.pending[head.op]
    if (text(head) === 'ver') this.onAnswer(line, now)
  }

  /** `ver` answered: an awake WW500 on its AI console. Read it, keep it awake, then stream. */
  private onAnswer(line: string, now: number): void {
    this.device.version = line.replace(/^\*+\s*|\s*\*+$/g, '')
    if (this.answered) return
    this.answered = true
    this.readOriginals()
    this.seq.push(verified(`setop 8 ${KEEP_AWAKE_MS}`, `Set OpParam 8 = ${KEEP_AWAKE_MS}`))
    STATUS_QUERIES.forEach(q => this.query(q))
    this.arm(now, 'connected')
  }

  /** "Command not recognised" right after the echo of the step at the head. */
  private unknownCommand(step: Step, now: number): void {
    this.seq.shift()
    this.seqLastSend = -Infinity
    if (step.op !== undefined) this.opFailed(step.op, `The camera does not know "${text(step)}".`)
    if (text(step) === PREVIEW_ON) {
      this.noPreview = true
      this.dropCaptures(now, 'this firmware has no live preview, so the page takes no pictures')
    } else if (!step.query) {
      this.note(now, `the camera does not know "${text(step)}"`)
    }
  }

  private dropCaptures(now: number, why: string): void {
    this.seq = this.seq.filter(s => !isCapture(s) && text(s) !== PREVIEW_ON)
    this.note(now, why)
  }

  /** A write from the controls refused or unanswered: the value goes back to the camera's, with why. */
  private opFailed(op: number, notice: string): void {
    if (this.pending[op] === undefined) return
    delete this.pending[op]
    this.opNotice = notice
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
    const wakeEvent = DEVICE_LINES.wakeReason.exec(line)
    if (wakeEvent) d.wakeReason = wakeEvent[1]
    const wake = DEVICE_LINES.wake.exec(line)
    if (wake) d.wakeReason = WAKE_WORDS[WAKE_CAUSE[wake[1]]]
    const slot = DEVICE_LINES.flashOffset.exec(line)
    if (slot) d.flashOffset = slot[1]
    const next = DEVICE_LINES.nextImage.exec(line)
    if (next) d.nextImage = Number(next[1])
    const dir = DEVICE_LINES.imagesDir.exec(line)
    if (dir) d.imagesDir = dir[1]
    if (DEVICE_LINES.sdReady.test(line)) d.sd = 'ready'
    if (DEVICE_LINES.sdFailed.test(line)) [d.sd, d.sdUsed] = ['failed', null]
    const used = DEVICE_LINES.sdUsed.exec(line)
    if (used) [d.sd, d.sdUsed] = Number(used[1]) <= 100 ? ['ready', Number(used[1])] : ['failed', null]
    const notWritten = DEVICE_LINES.imageNotWritten.exec(line)
    if (notWritten && (notWritten[1] === '3' || notWritten[1] === '13')) [d.sd, d.sdUsed] = ['failed', null]
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
    if (this.pending[op] === value) delete this.pending[op]
    if (op === OP_MD_INTERVAL && value !== this.op11) {
      this.op11 = value
      this.intervalChanged(now)
    }
  }

  /** The stream's interval: the box's value, else the camera type, op 11 and the grid switch. */
  private intervalChanged(now: number): void {
    const ms = this.userIntervalMs ?? captureIntervalMs(this.camera, this.op11, this.mdOn)
    if (ms === this.intervalMs) return
    this.intervalMs = ms
    this.note(now, `${this.camera ?? 'camera'}: a frame every ${ms} ms`)
    // A wake's own burst keeps its interval; the page's next capture takes the new one.
    if (this.captureBusy && this.ownBurst && !this.coldBoot && !this.stopping) this.query(`interval ${ms}`)
  }

  /** `### Cold Boot ###` or `### Warm Boot ###`: whatever was queued was for the camera before. */
  private onBoot(cold: boolean, now: number): void {
    this.answered = true
    this.dpdPending = false
    this.asleep = false
    this.coldBoot = cold
    this.captureBusy = false
    this.ownBurst = false
    this.previewOn = false
    // The "no frames" check waits for this boot's first frame.
    this.lastFrameAt = -Infinity
    this.device.boot = cold ? 'cold' : 'warm'
    this.wokeBy = null
    this.boot = { cold, cli: false, camera: false, card: false }
    this.inFlight = null
    if (this.stopping) return
    this.seq = []
    if (Object.keys(this.pending).length) {
      this.pending = {}
      this.opNotice = 'The camera restarted before it confirmed the change.'
    }
    if (cold) this.startup = { restarted: true, sleepOnce: true, asleepAt: null, woke: false }
    else if (this.startup) this.startup.woke = true
    else if (this.keepAwake) this.startup = { restarted: false, sleepOnce: false, asleepAt: null, woke: true }
    this.note(now, cold
      ? 'cold boot: it takes no pictures until it has slept once, so it sleeps once it is ready and wakes 10 s later'
      : this.keepAwake ? 'the camera woke: preview on, Free run once it is ready' : 'the camera woke: preview on, to show its own pictures')
  }

  /** A boot's command line, camera and card, each as the camera reports it ready. */
  private bootProgress(line: string, now: number): void {
    const b = this.boot
    if (!b) return
    // The camera's ready line means the command line started before it, if that line was lost.
    if (!b.cli && (LINES.cliUp.test(line) || LINES.bootDone.test(line))) {
      b.cli = true
      if (!this.stopping) this.cliUp(b.cold)
    }
    if (LINES.bootDone.test(line)) b.camera = true
    if (LINES.cardDone.test(line)) b.card = true
    if (!b.cli || !b.camera || !b.card) return
    this.boot = null
    if (!this.stopping) this.ready(b.cold, now)
  }

  /** The command line is up: after a wake, preview first, as every sleep clears it and the wake's
   *  own burst would otherwise be saved to the SD card. A wake starts Free run again, unless Keep
   *  awake is off. */
  private cliUp(cold: boolean): void {
    if (cold) return
    if (this.keepAwake) {
      this.runMode = 'free'
      this.oneShotPending = false
    }
    if (!this.noPreview) this.seq.push(verified(PREVIEW_ON, 'Preview mode 1'))
  }

  /** Camera and card ready, CONFIG.TXT loaded: the writes go now. Cold: the wake, then sleep.
   *  Warm: op 7 back if it is the first sleep's 10 s wake, keep awake, read it, then stream. */
  private ready(cold: boolean, now: number): void {
    this.readOriginals()
    if (cold) {
      this.sleepNow(now)
      return
    }
    // Put back now rather than at Disconnect, so later sleeps wake on motion and a session that
    // ends without Disconnect does not leave a 10 s timelapse.
    const op7 = this.ops[7] === WAKE_SECONDS ? this.op7Back() : null
    if (op7) this.seq.push(op7)
    this.seq.push(verified(`setop 8 ${KEEP_AWAKE_MS}`, `Set OpParam 8 = ${KEEP_AWAKE_MS}`))
    STATUS_QUERIES.forEach(q => this.query(q))
    this.arm(now, 'camera ready', !this.previewOn)
  }

  /** Op 7 as found, or no timelapse in place of a 10 s wake an earlier session left; null when it
   *  is that already, or was neither read nor written. */
  private op7Back(): Step | null {
    const op7 = this.original[7] === undefined || this.original[7] === WAKE_SECONDS ? 0 : this.original[7]
    if (this.ops[7] === op7 || (this.ops[7] === undefined && !this.written.has(7))) return null
    return verified(`setop 7 ${op7}`, `Set OpParam 7 = ${op7}`)
  }

  private sleepNow(now: number): void {
    this.readOriginals()
    if (this.ops[7] !== WAKE_SECONDS && !this.seq.some(s => text(s) === `setop 7 ${WAKE_SECONDS}`)) {
      this.seq.push(verified(`setop 7 ${WAKE_SECONDS}`, `Set OpParam 7 = ${WAKE_SECONDS}`))
    }
    this.note(now, `sending the camera to sleep: it wakes ${WAKE_SECONDS} s later`)
    this.seq.push(verified('dpd', 'Forcing DPD'))
  }

  private wentToSleep(now: number): void {
    this.captureBusy = false
    this.ownBurst = false
    this.previewOn = false
    this.wokeBy = null
    if (this.dpdPending || this.coldBoot) {
      if (this.startup?.sleepOnce && !this.startup.woke) this.startup.asleepAt = now
      return
    }
    this.asleep = true
    this.note(now, this.keepAwake
      ? 'the camera went to sleep: motion or its timer wakes it, and Free run starts again'
      : 'the camera went to sleep: motion or its timer wakes it')
  }

  /** Queues the next capture: a burst in Free run, one frame for One image, nothing when stopped,
   *  during a boot (its ready line arms) or without preview. */
  private arm(now: number, reason: string, withPreview = true): void {
    if (this.coldBoot) {
      this.note(now, `${reason}: no capture yet, the camera has to sleep once first`)
      return
    }
    if (this.boot || this.noPreview) return
    if (this.runMode === 'paused' || (this.runMode === 'one' && !this.oneShotPending)) return
    if (this.seq.some(isCapture)) return
    if (this.captureBusy && now - Math.max(this.lastFrameAt, this.busyAt) > REARM_QUIET_MS) this.captureBusy = false
    if (this.captureBusy) return
    const frames = this.runMode === 'one' ? 1 : BURST_FRAMES
    if (this.runMode === 'one') this.oneShotPending = false
    const capture = verified(() => `capture ${frames} ${this.intervalMs}`, 'About to capture')
    const preview = withPreview && !this.seq.some(s => text(s) === PREVIEW_ON)
    this.seq.push(...(preview ? [verified(PREVIEW_ON, 'Preview mode 1'), capture] : [capture]))
  }

  private burstEnded(now: number): void {
    this.captureBusy = false
    this.ownBurst = false
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
    this.seq = this.noPreview ? [] : [verified('preview 0', 'Preview off')]
    const op7 = this.op7Back()
    if (op7) this.seq.push(op7)
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
      : !this.answered ? 'disconnected: the camera never answered'
        : this.unconfirmed.length ? `disconnected, not confirmed: ${this.unconfirmed.join(', ')}` : 'disconnected: the camera is asleep')
  }
}

/** What stands over the picture in place of the steps. */
export const LIVE_VIEW_WARNINGS: Record<LiveViewWarning, string> = {
  asleep: 'The camera is asleep. Press its RESET button.',
  'other-port': 'No answer: this may be the other port. Disconnect, then connect and pick the other one.',
  'no-preview': 'This firmware has no live preview, so the page takes no pictures.',
}

const WOKE: Record<WakeCause, string> = {
  motion: 'Woke on motion.', timer: 'Woke on its timer.', bluetooth: 'Woken by the Bluetooth chip.',
}

/** The one line beside the Connect button, or null when the picture says it all. */
export function liveViewStatus(view: LiveViewSnapshot | null): string | null {
  if (!view) return "Connect, then press the camera's RESET button if it is asleep."
  if (view.phase !== 'stopping' && view.phase !== 'stopped' && (view.warning || view.steps)) return null
  const woke = !view.keepAwake && view.wokeBy ? `${WOKE[view.wokeBy]} ` : ''
  switch (view.phase) {
    case 'starting':
      return 'Waiting for the camera…'
    case 'sleeping':
      return view.sleepReason === 'idle'
        ? `The camera is asleep. Motion or its timer wakes it${view.keepAwake ? ', and Free run starts again' : ''}.`
        : `The camera sleeps once after it starts, then wakes ${WAKE_SECONDS} s later.`
    case 'streaming':
      if (view.noPictureFor !== null) return `No picture for ${view.noPictureFor} s.`
      return view.runMode === 'free' ? null : `${woke}Finishing the current burst…`
    case 'paused':
      return view.keepAwake
        ? 'Stopped. Free run or One image takes more pictures.'
        : `${woke}Stopped with Keep awake off: the camera sleeps after a minute without commands.`
    case 'stopping':
      return 'Disconnecting after the current pictures…'
    case 'stopped':
      if (view.lost) return 'The connection closed. Reconnect and press Disconnect, so the camera sleeps normally.'
      if (!view.answered) return 'Disconnected. The camera never answered, so nothing on it changed.'
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
