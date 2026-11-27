import { describe, expect, it } from 'vitest'
import { LIVE_VIEW_STALLED, LIVE_VIEW_WARNINGS, LiveViewSession, liveViewStatus, logText } from './liveViewSession'

const JPEG_B64 = '/9j/4AAQSkZJRg=='

/** A frame line as preview.c prints it, with motion in the given console bits and threshold thA. */
function frameLine(bits: number[] = [], thA = 4): string {
  const md = new Uint8Array(32)
  for (const b of bits) md[b >> 3] |= 1 << (b & 7)
  const hex = [...md].map(x => x.toString(16).padStart(2, '0')).join('')
  return `\r{"type": 1, "name": "INVOKE", "code": 0, "data": {"count": 3, "resolution": [640, 480], "boxes": [], `
    + `"md": "${hex}", "mdstat": {"int": 0, "ctrl": 1, "ctrl1": 2, "thA": ${thA}, "thB": ${thA}}, "image": "${JPEG_B64}"}}`
}

/** An awake HM0360 camera's answers, as the firmware prints them at e3c4d18a. */
const AWAKE: Record<string, string> = {
  ver: 'WW500-A00 V 00.08.00 18:29:31 Mar 26 2025',
  'getop 7': 'OpParam 7 = 0',
  'getop 8': 'OpParam 8 = 1000',
  'setop 8 60000': 'Set OpParam 8 = 60000',
  camera: 'HM0360',
  'getop 10': 'OpParam 10 = 1',
  'getop 11': 'OpParam 11 = 500',
  'getop 17': 'OpParam 17 = 2',
  'getop 33': 'OpParam 33 = 4',
  sdused: 'sdused 12',
  'setop 7 10': 'Set OpParam 7 = 10',
  'setop 7 0': 'Set OpParam 7 = 0',
  'setop 8 1000': 'Set OpParam 8 = 1000',
  'preview 1': "Preview mode 1 (SD save skipped). Start frames with e.g. 'capture 1000 0'.",
  'preview 0': 'Preview off.',
  'capture 10 500': "About to capture 10 images with an interval of '500' milliseconds",
  'capture 10 1000': "About to capture 10 images with an interval of '1000' milliseconds",
  dpd: 'Forcing DPD by clearing inactivity period',
}

const QUERIES = ['camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33', 'sdused']
const CONNECT = ['ver', 'getop 7', 'getop 8', 'setop 8 60000', ...QUERIES]
const NOT_RECOGNISED = "Command not recognised.  Enter 'help' to view a list of available commands."
/** The camera and the card ready after a boot, in the bench's order (27 Nov 2026). */
const READY = ['Image sensor and data path initialised for camera HM0360', 'Inactivity period set at 1000ms']

/** A session on a simulated clock. `run` types what the session asks for (30 ms a character) and
 *  feeds back the camera's answer from `replies`, if it has one. With `replyFirst` the answer
 *  arrives before `typed()`, as on the bench: the camera answers inside the last character's pause. */
function bench(replies: Record<string, string> = AWAKE, { replyFirst = false } = {}) {
  const s = new LiveViewSession()
  let t = 1_000_000
  const sent: string[] = []
  const feed = (...lines: string[]) => lines.forEach(line => s.handleLine(line, t))
  const run = (ms: number) => {
    const end = t + ms
    while (t < end) {
      s.tick(t)
      const out = s.takeCommand(t)
      if (out === null) {
        t += 50
        continue
      }
      t += out.length * 30
      const cmd = out.trim()
      const reply = replies[cmd]
      if (replyFirst && reply !== undefined) feed(reply)
      s.typed(out, t)
      sent.push(cmd || 'CR')
      if (!replyFirst && reply !== undefined) feed(reply)
    }
    s.tick(t)
  }
  const take = () => sent.splice(0)
  return { s, feed, run, take, now: () => t, view: () => s.snapshot() }
}

/** Connected to an awake camera, streaming. */
function streaming() {
  const b = bench()
  b.s.start(b.now())
  b.run(10000)
  b.take()
  b.feed(frameLine())
  return b
}

describe('LiveViewSession, connecting', () => {
  it('reads the camera, keeps it awake and starts the stream', () => {
    const b = bench()
    b.s.start(b.now())
    b.run(10000)
    // The capture interval follows op 11 on the HM0360, read before the capture is typed.
    expect(b.take()).toEqual([...CONNECT, 'preview 1', 'capture 10 500'])
    expect(b.view()).toMatchObject({
      phase: 'starting', camera: 'HM0360', ops: { 10: 1, 11: 500, 17: 2, 33: 4 }, device: { sd: 'ready', sdUsed: 12 },
    })
    expect(b.view().steps).toEqual([{ label: 'Camera answers', state: 'done' }, { label: 'First picture', state: 'now' }])
  })

  it('shows each frame with its motion cells and wake verdict', () => {
    const b = streaming()
    expect(b.view().frame).toMatchObject({ image: JPEG_B64, cells: [], threshold: 4, wake: false, width: 640 })
    b.feed(frameLine([0, 1, 2, 3, 4]))
    expect(b.view()).toMatchObject({ phase: 'streaming', steps: null, frame: { cells: [251, 252, 253, 254, 255], wake: true } })
  })

  it('counts a damaged frame and keeps the last good picture', () => {
    const b = streaming()
    b.feed('{"type": 1, "name": "INVOKE", "data": {"image": "/9j/[IF] sent"}}')
    expect(b.view()).toMatchObject({ dropped: 1, frame: { image: JPEG_B64 } })
  })

  it('starts the next burst when one ends, without preview again', () => {
    const b = streaming()
    b.feed('Current captures completed: 10')
    b.run(1000)
    expect(b.take()).toEqual(['capture 10 500'])
  })

  it('types each step as soon as the last one is answered, even inside its last pause', () => {
    // Bench, 26 Oct 2026: each answer came within 0.4 s, but every step waited the 3 s retry.
    const b = bench(AWAKE, { replyFirst: true })
    b.s.start(b.now())
    b.run(5000)
    expect(b.take()).toEqual([...CONNECT, 'preview 1', 'capture 10 500'])
  })

  it('a sleeping camera: says to press RESET, and types nothing but ver into it', () => {
    // Bench, 27 Nov 2026: Chrome opened the port without a reset, and the page typed ver, setop 8
    // and preview 1 into the sleeping camera for 95 s under a grey "probably asleep".
    const b = bench({})
    b.s.start(b.now())
    b.run(9000)
    expect(b.view().warning).toBeNull()
    b.run(30000)
    expect(new Set(b.take())).toEqual(new Set(['ver']))
    const view = b.view()
    expect(view.warning).toBe('asleep')
    expect(LIVE_VIEW_WARNINGS[view.warning ?? 'asleep']).toBe('The camera is asleep. Press its RESET button.')
    expect(liveViewStatus(view)).toBeNull()
  })

  it('does not take the boot banner for the answer to ver', () => {
    const b = bench({})
    b.s.start(b.now())
    b.run(100)
    b.feed('**** WW500 MD. (WW500_C02) Built: 11:33:56 Nov 21 2026 ****')
    b.run(4000)
    expect(b.take()).toEqual(['ver', 'ver'])
  })

  it('says before Connect to press RESET if the camera is asleep', () => {
    expect(liveViewStatus(null)).toBe("Connect, then press the camera's RESET button if it is asleep.")
  })
})

describe('LiveViewSession, the bench check (#528)', () => {
  it("the Bluetooth chip's port: lines but no answer, so it says it may be the other port", () => {
    const b = bench({})
    b.s.start(b.now())
    for (let i = 0; i < 12; i++) {
      b.feed('àø\u0080 þ')
      b.run(1000)
    }
    expect(new Set(b.take())).toEqual(new Set(['ver']))
    const view = b.view()
    expect(view.warning).toBe('other-port')
    expect(LIVE_VIEW_WARNINGS['other-port']).toMatch(/^No answer: this may be the other port/)
    expect(liveViewStatus(view)).toBeNull()
  })

  it('the AI port: cold boot, sleep, a wake about 10 s later, frames, op 7 back at once', () => {
    const b = bench()
    b.s.start(b.now())
    // RESET: the Bluetooth chip resets the AI processor too, so it boots twice.
    b.feed('### Cold Boot ###', '### Cold Boot ###', 'Starting CLI Task')
    b.run(2000)
    expect(b.take()).toEqual([]) // nothing is written before CONFIG.TXT is loaded
    expect(b.view().steps).toEqual([
      { label: 'Camera restarts', state: 'done' },
      { label: 'Sleeps once first', state: 'now' },
      { label: 'Wakes 10 s later', state: 'todo' },
      { label: 'First picture', state: 'todo' },
    ])
    b.feed(...READY)
    b.run(3000)
    expect(b.take()).toEqual(['getop 7', 'getop 8', 'setop 7 10', 'dpd'])
    expect(b.view().phase).toBe('sleeping')
    b.feed('>>> Entering DPD at 2024:01:01 00:00:08')
    const asleepAt = b.now()
    b.run(3000)
    b.s.tick(asleepAt + 3000)
    expect(b.view().steps?.[2]).toEqual({ label: 'Wakes in 7 s', state: 'now' })
    expect(liveViewStatus(b.view())).toBeNull() // the steps say it
    b.run(7000)
    expect(b.take()).toEqual([]) // no keep-awake while it sleeps

    b.feed('### Warm Boot ###', 'Timer wake', 'Starting CLI Task')
    b.run(500)
    expect(b.take()).toEqual(['preview 1'])
    // The wake's own burst: the capture waits for it to end.
    b.feed(READY[0], 'Images to capture: 1', 'Inactivity period set at 3000ms')
    b.run(4000)
    expect(b.take()).toEqual(['setop 7 0', 'setop 8 60000', ...QUERIES])
    b.feed(frameLine(), 'Current captures completed: 1')
    b.run(1000)
    expect(b.take()).toEqual(['capture 10 500'])
    expect(b.view()).toMatchObject({ phase: 'streaming', steps: null, ops: { 7: 0 }, runMode: 'free' })
    expect(liveViewStatus(b.view())).toBeNull()
  })

  it('Stop, wait 15 s, vcm 512: "Command not recognised" in the log, and still stopped', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.run(15000)
    b.take()
    b.s.sendCommand('vcm 512', b.now())
    b.run(500)
    expect(b.take()).toEqual(['vcm 512'])
    b.feed('cmd> vcm 512', NOT_RECOGNISED)
    b.run(15000)
    expect(b.take().filter(c => c !== 'CR')).toEqual([])
    expect(b.view()).toMatchObject({ phase: 'paused', runMode: 'paused', steps: null })
    expect(b.s.fullLog().some(l => l.text === NOT_RECOGNISED)).toBe(true)
    expect(b.s.fullLog().some(l => /woke/.test(l.text))).toBe(false)
  })

  it('RESET mid-stream, within a second of a frame: sleeps, wakes and streams', () => {
    const b = streaming()
    b.run(500)
    b.take()
    b.feed(frameLine(), '### Cold Boot ###', 'Starting CLI Task', ...READY)
    b.run(3000)
    // Ops 7 and 8 were read at Connect, so not again.
    expect(b.take()).toEqual(['setop 7 10', 'dpd'])
    expect(b.view()).toMatchObject({ phase: 'sleeping', steps: [{ label: 'Camera restarts', state: 'done' }, {}, {}, {}] })
    b.feed('>>> Entering DPD at 2024:01:01 00:00:06')
    b.run(10000)
    b.feed('### Warm Boot ###', 'Timer wake', 'Starting CLI Task', ...READY)
    b.run(5000)
    expect(b.take()).toEqual(['preview 1', 'setop 7 0', 'setop 8 60000', ...QUERIES, 'capture 10 500'])
    b.feed(frameLine())
    expect(b.view()).toMatchObject({ phase: 'streaming', steps: null })
  })

  it('Stop, untick Keep awake: sleeps, wakes on motion, shows its pictures and stays stopped', () => {
    const b = streaming()
    b.s.setKeepAwake(false, b.now())
    expect(b.view().runMode).toBe('paused') // Free run's captures would keep it awake
    b.feed('Current captures completed: 10')
    b.run(25000)
    expect(b.take()).toEqual([]) // no CR, no capture
    expect(liveViewStatus(b.view())).toMatch(/Keep awake off: the camera sleeps after a minute/)
    b.feed('>>> Entering DPD at 2024:01:01 00:01:12')
    expect(b.view()).toMatchObject({ phase: 'sleeping', sleepReason: 'idle' })
    expect(liveViewStatus(b.view())).toBe('The camera is asleep. Motion or its timer wakes it.')

    b.feed('### Warm Boot ###', 'Motion detected INT_INDIC = 0x01', 'Starting CLI Task')
    b.run(500)
    expect(b.take()).toEqual(['preview 1'])
    b.feed(READY[0], 'Images to capture: 1', READY[1])
    b.run(4000)
    // Op 7 was 0, so it stays; no capture of the page's own.
    expect(b.take()).toEqual(['setop 8 60000', ...QUERIES])
    b.feed(frameLine([0, 1, 2, 3, 4]))
    expect(liveViewStatus(b.view())).toBe('Woke on motion. Finishing the current burst…')
    b.feed('Current captures completed: 1')
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(b.view()).toMatchObject({ phase: 'paused', wokeBy: 'motion', device: { boot: 'warm', wakeReason: 'motion' } })
    expect(liveViewStatus(b.view())).toMatch(/^Woke on motion\. Stopped with Keep awake off/)
  })

  it('the camera switched off with the cable in: "No picture for N s"', () => {
    const b = streaming()
    b.run(3000)
    expect(liveViewStatus(b.view())).toBeNull()
    b.run(5000)
    const view = b.view()
    expect(view.noPictureFor).toBeGreaterThanOrEqual(7)
    expect(liveViewStatus(view)).toBe(`No picture for ${view.noPictureFor} s.`)
  })

  it('unplugged: says to reconnect', () => {
    const b = streaming()
    b.s.disconnected(b.now())
    expect(liveViewStatus(b.view())).toMatch(/connection closed\. Reconnect/)
  })

  it('Disconnect after a cold-boot start: preview 0, op 8 back (op 7 went back at the wake), dpd', () => {
    const b = bench()
    b.s.start(b.now())
    b.feed('### Cold Boot ###', 'Starting CLI Task', ...READY)
    b.run(3000)
    b.feed('>>> Entering DPD at 2024:01:01 00:00:08', '### Warm Boot ###', 'Timer wake', 'Starting CLI Task', ...READY)
    b.run(5000)
    b.feed(frameLine())
    b.take()
    b.s.disconnect(b.now())
    b.feed('Current captures completed: 10')
    b.run(3000)
    expect(b.take()).toEqual(['preview 0', 'setop 8 1000', 'dpd'])
    expect(liveViewStatus(b.view())).toMatch(/asleep with its sleep settings put back/)
  })
})

describe('LiveViewSession, boots', () => {
  it('types nothing between a boot line and its command line', () => {
    const b = bench({})
    b.s.start(b.now())
    b.run(100)
    b.take()
    b.feed('### Warm Boot ###')
    b.run(10000)
    expect(b.take()).toEqual([])
    b.feed('Starting CLI Task')
    b.run(100)
    expect(b.take()).toEqual(['preview 1'])
  })

  it('after the wake burst, reads the settings at once rather than 3 s after its last frame', () => {
    const b = bench(AWAKE, { replyFirst: true })
    b.s.start(b.now())
    b.feed('### Warm Boot ###', 'Starting CLI Task', READY[0], 'Images to capture: 3', READY[1])
    b.run(200)
    b.feed(frameLine(), 'Current captures completed: 3')
    // Eleven steps of about 0.35 s each; waiting 3 s after the last frame for each took 27 s.
    b.run(4500)
    expect(b.take()).toEqual(['preview 1', 'getop 7', 'getop 8', 'setop 8 60000', ...QUERIES, 'capture 10 500'])
  })

  it('does not ask again for frames right after a wake: that waits for its first frame', () => {
    // Bench, 27 Nov 2026: "no frames for 20 s: asking again" 3 s after a wake, as the last frame
    // was from before the reset; the next frame was damaged.
    const b = streaming()
    b.run(30000)
    b.feed('### Cold Boot ###', 'Starting CLI Task', ...READY)
    b.run(3000)
    b.feed('>>> Entering DPD at 2024:01:01 00:00:06', '### Warm Boot ###', 'Timer wake', 'Starting CLI Task')
    b.run(500)
    b.feed(READY[0], 'Images to capture: 1', READY[1])
    b.run(5000)
    expect(b.s.fullLog().filter(l => /asking again/.test(l.text))).toHaveLength(1) // the one before the reset
  })

  it('a lost ready line stalls the start: after 10 s of silence it says to press RESET, and types nothing', () => {
    const b = bench(AWAKE, { replyFirst: true })
    b.s.start(b.now())
    // `Inactivity period set at` never arrives, so the writes wait for it.
    b.feed('### Warm Boot ###', 'Starting CLI Task', READY[0])
    b.run(500)
    expect(b.take()).toEqual(['preview 1'])
    b.run(9000)
    expect(b.view().stalled).toBe(false)
    b.run(2000)
    expect(b.view().stalled).toBe(true)
    expect(b.view().steps?.at(-1)).toEqual({ label: 'First picture', state: 'now' })
    expect(LIVE_VIEW_STALLED).toMatch(/press the camera's RESET button/)
    b.run(30000)
    expect(b.take()).toEqual([])
    // RESET: the next boot moves the steps again.
    b.feed('### Warm Boot ###')
    expect(b.view().stalled).toBe(false)
  })

  it("says to press RESET only when the first sleep's wake is 10 s late", () => {
    const b = bench({ ...AWAKE, 'capture 10 500': 'Error: no captures until after the first sleep.' })
    b.s.start(b.now())
    b.run(10000)
    b.feed('>>> Entering DPD at 2024:01:01 00:00:06')
    b.run(19000)
    expect(b.view().stalled).toBe(false)
    b.run(2000)
    expect(b.view().stalled).toBe(true)
    expect(b.view().steps?.[2]).toEqual({ label: 'Wakes any moment', state: 'now' })
  })

  it('when the camera refuses a capture, sends it to sleep with the wake set', () => {
    const b = bench({ ...AWAKE, 'capture 10 500': 'Error: no captures until after the first sleep.' })
    b.s.start(b.now())
    b.run(10000)
    expect(b.take()).toEqual([...CONNECT, 'preview 1', 'capture 10 500', 'setop 7 10', 'dpd'])
    expect(b.view().steps).toEqual([
      { label: 'Camera answers', state: 'done' },
      { label: 'Sleeps once first', state: 'now' },
      { label: 'Wakes 10 s later', state: 'todo' },
      { label: 'First picture', state: 'todo' },
    ])
  })

  it('firmware without preview: says so, and types no capture, which would save to the SD card', () => {
    const b = bench(Object.fromEntries(Object.entries(AWAKE).filter(([cmd]) => cmd !== 'preview 1')))
    b.s.start(b.now())
    b.run(5000)
    expect(b.take().at(-1)).toBe('preview 1')
    // The console echoes the command, then answers it.
    b.feed('cmd> preview 1', NOT_RECOGNISED)
    b.run(5000)
    expect(b.take()).toEqual([])
    expect(b.view().warning).toBe('no-preview')
    expect(LIVE_VIEW_WARNINGS['no-preview']).toMatch(/no live preview/)
    b.s.disconnect(b.now())
    b.run(2000)
    expect(b.take()).toEqual(['setop 8 1000', 'dpd'])
  })
})

describe('LiveViewSession, settings', () => {
  it('shows a write as pending until the camera confirms it', () => {
    const b = streaming()
    b.s.setOp(33, 6, b.now())
    expect(b.view()).toMatchObject({ ops: { 33: 4 }, pendingOps: { 33: 6 } })
    b.feed(frameLine())
    b.run(400)
    b.feed('Set OpParam 33 = 6')
    expect(b.view()).toMatchObject({ ops: { 33: 6 }, pendingOps: {}, opNotice: null })
  })

  it('says when the camera refuses a value, and shows its own again', () => {
    const b = streaming()
    b.s.setOp(33, 6, b.now())
    // One step after each frame: the console is free between frames.
    b.run(500)
    b.feed('Error: 6 is not a valid value for op 33.')
    b.feed(frameLine())
    b.run(500)
    b.feed('OpParam 33 = 4')
    expect(b.take()).toEqual(['setop 33 6', 'getop 33'])
    expect(b.view()).toMatchObject({ ops: { 33: 4 }, pendingOps: {}, opNotice: 'The camera refused 6 for the wake threshold.' })
  })

  it('says when a write gets no answer', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.s.setOp(17, 3, b.now())
    b.run(30000)
    expect(b.view()).toMatchObject({ ops: { 17: 2 }, pendingOps: {}, opNotice: 'No answer to "setop 17 3" from the camera.' })
  })

  it('refuses a value out of range without typing it', () => {
    const b = streaming()
    b.s.setOp(17, 4, b.now())
    b.run(500)
    expect(b.take()).toEqual([])
  })

  it('changes the stream interval with op 11, from the next frame', () => {
    const b = streaming()
    b.feed('Set OpParam 11 = 1500')
    b.feed(frameLine())
    b.run(500)
    expect(b.take()).toEqual(['interval 1500'])
  })

  it('keeps the camera awake with a bare CR when nothing else is sent', () => {
    const b = bench()
    b.s.start(b.now())
    b.run(25000)
    expect(b.take().filter(c => c === 'CR')).toHaveLength(1)
  })

  it('reads the SD card from sdused, and a missing one from each frame\'s write error', () => {
    const b = bench({ ...AWAKE, sdused: 'sdused 255' })
    b.s.start(b.now())
    b.run(10000)
    expect(b.view().device).toMatchObject({ sd: 'failed', sdUsed: null })

    const old = bench({ ...AWAKE, sdused: NOT_RECOGNISED })
    old.s.start(old.now())
    old.run(10000)
    expect(old.view().device.sd).toBeNull()
    old.feed('Skipping file save.', 'Image not written. Error: 13')
    expect(old.view().device.sd).toBe('failed')
  })
})

describe('LiveViewSession, stopping', () => {
  it('waits for the burst, then turns preview off, puts op 8 back and sends it to sleep', () => {
    const b = streaming()
    b.s.disconnect(b.now())
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(liveViewStatus(b.view())).toMatch(/Disconnecting/)
    b.feed('Current captures completed: 10')
    b.run(2000)
    expect(b.take()).toEqual(['preview 0', 'setop 8 1000', 'dpd'])
    const view = b.view()
    expect(view).toMatchObject({ phase: 'stopped', unconfirmed: [] })
    expect(liveViewStatus(view)).toMatch(/asleep with its sleep settings put back/)
    expect(b.s.takeCommand(b.now())).toBeNull()
  })

  it('waits for a burst whose capture was typed but not yet acknowledged', () => {
    // Bench, 26 Oct 2026: Stop 0.5 s after `capture` sent `preview 0` before the camera's answer,
    // and the burst then saved its photos to the SD card.
    const noAck = Object.fromEntries(Object.entries(AWAKE).filter(([cmd]) => cmd !== 'capture 10 500'))
    const b = bench(noAck)
    b.s.start(b.now())
    b.run(10000)
    expect(b.take().at(-1)).toBe('capture 10 500')
    b.s.disconnect(b.now())
    b.run(500)
    expect(b.take()).toEqual([])
    b.feed(AWAKE['capture 10 500'], 'Images to capture: 10', frameLine())
    b.run(2000)
    expect(b.take()).toEqual([])
    b.feed('Current captures completed: 10')
    b.run(2000)
    expect(b.take()).toEqual(['preview 0', 'setop 8 1000', 'dpd'])
  })

  it('stops anyway when a typed capture is never acknowledged', () => {
    const noAck = Object.fromEntries(Object.entries(AWAKE).filter(([cmd]) => cmd !== 'capture 10 500'))
    const b = bench(noAck)
    b.s.start(b.now())
    b.run(10000)
    b.take()
    b.s.disconnect(b.now())
    b.run(6000)
    expect(b.take()).toEqual(['preview 0', 'setop 8 1000', 'dpd'])
  })

  it('puts back op 7 when it disconnects during the first sleep, with quiet values over an earlier session\'s', () => {
    const b = bench({ ...AWAKE, 'getop 7': 'OpParam 7 = 10', 'getop 8': 'OpParam 8 = 60000' })
    b.s.start(b.now())
    b.feed('### Cold Boot ###', 'Starting CLI Task', ...READY)
    b.run(2000)
    // Op 7 is read as 10, left by an earlier session, after the wake was queued: setting it again does no harm.
    expect(b.take()).toEqual(['getop 7', 'getop 8', 'setop 7 10', 'dpd'])
    b.feed('>>> Entering DPD at 2024:01:01 00:00:06')
    b.s.disconnect(b.now())
    b.run(3000)
    expect(b.take()).toEqual(['preview 0', 'setop 7 0', 'dpd'])
  })

  it('clears a 10 s wake an earlier session left, even when this one did not set it', () => {
    const b = bench({ ...AWAKE, 'getop 7': 'OpParam 7 = 10', 'getop 8': 'OpParam 8 = 60000' })
    b.s.start(b.now())
    b.run(10000)
    b.take()
    b.s.disconnect(b.now())
    b.feed('Current captures completed: 10')
    b.run(3000)
    expect(b.take()).toEqual(['preview 0', 'setop 7 0', 'setop 8 1000', 'dpd'])
  })

  it('lists what the camera never confirmed', () => {
    const b = bench({ ver: AWAKE.ver })
    b.s.start(b.now())
    b.run(1000)
    b.s.disconnect(b.now())
    b.run(31000)
    const view = b.view()
    // Op 8 was not written before Stop, so it is not put back.
    expect(view.phase).toBe('stopped')
    expect(view.unconfirmed).toEqual(['preview 0', 'dpd'])
    expect(liveViewStatus(view)).toMatch(/did not confirm: preview 0, dpd/)
  })

  it('disconnects at once from a camera that never answered: the other port, or asleep', () => {
    const b = bench({})
    b.s.start(b.now())
    b.feed('àø\u0080 þ')
    b.run(12000)
    b.take()
    b.s.disconnect(b.now())
    expect(b.view().phase).toBe('stopped')
    expect(b.s.takeCommand(b.now())).toBeNull()
    expect(liveViewStatus(b.view())).toBe('Disconnected. The camera never answered, so nothing on it changed.')
  })
})

describe('LiveViewSession, run modes', () => {
  it('Stop lets the burst finish and arms no more, with preview still on', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(b.view()).toMatchObject({ phase: 'paused', runMode: 'paused' })
    expect(liveViewStatus(b.view())).toMatch(/Free run or One image/)
  })

  it('Free run after Stop starts a burst without typing preview again', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.run(500)
    b.take()
    b.s.freeRun(b.now())
    b.run(1000)
    expect(b.take()).toEqual(['capture 10 500'])
  })

  it('One image takes a single frame after the current burst, then stops', () => {
    const b = streaming()
    b.s.oneImage(b.now())
    b.run(500)
    expect(b.take()).toEqual([])
    b.feed('Current captures completed: 10')
    b.run(1000)
    expect(b.take()).toEqual(['capture 1 500'])
    b.feed("About to capture 1 images with an interval of '500' milliseconds", frameLine(), 'Current captures completed: 1')
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(b.view().phase).toBe('paused')
  })

  it('types a command from the box once, without holding up the next', () => {
    const b = bench(AWAKE, { replyFirst: true })
    b.s.start(b.now())
    b.run(5000)
    b.take()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.s.sendCommand('status', b.now())
    b.s.sendCommand('getop 27', b.now())
    b.run(1000)
    expect(b.take()).toEqual(['status', 'getop 27'])
  })

  it('changes the frame interval from the box, from the next frame and for the next burst', () => {
    const b = streaming()
    b.s.setFrameInterval(200, b.now())
    b.feed(frameLine())
    b.run(500)
    expect(b.take()).toEqual(['interval 200'])
    expect(b.view()).toMatchObject({ intervalMs: 200, intervalSet: true })
    b.feed('Current captures completed: 10')
    b.run(1000)
    expect(b.take()).toEqual(['capture 10 200'])
  })

  it('refuses a frame interval longer than 10 s, which the 20 s "no frames" check would cut into', () => {
    const b = streaming()
    b.s.setFrameInterval(60000, b.now())
    expect(b.view()).toMatchObject({ intervalMs: 500, intervalSet: false })
  })

  it('with the motion grid off streams plain video at 20 ms and counts no wakes', () => {
    const b = streaming()
    b.feed(frameLine([0, 1, 2, 3, 4]))
    expect(b.view().md).toEqual({ frames: 2, wakes: 1 })
    b.s.setMotionDetection(false, b.now())
    b.feed(frameLine([0, 1, 2, 3, 4]))
    b.run(500)
    expect(b.take()).toEqual(['interval 20'])
    expect(b.view()).toMatchObject({ motionDetection: false, intervalMs: 20, intervalSet: false, md: { frames: 0, wakes: 0 } })
  })

  it('after sleeping on its own with Keep awake on and waking, starts Free run again', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10', '>>> Entering DPD at 2024:01:01 00:00:12')
    expect(b.view()).toMatchObject({ phase: 'sleeping', sleepReason: 'idle' })
    expect(liveViewStatus(b.view())).toMatch(/Motion or its timer wakes it, and Free run starts again/)
    b.run(11000)
    expect(b.take()).toEqual([]) // nothing typed while it sleeps
    b.feed('### Warm Boot ###', 'Timer wake', 'Starting CLI Task', ...READY)
    b.run(5000)
    expect(b.take()).toEqual(['preview 1', 'setop 8 60000', ...QUERIES, 'capture 10 500'])
    expect(b.view()).toMatchObject({ runMode: 'free', steps: [{ label: 'Camera wakes', state: 'done' }, { label: 'First picture', state: 'now' }] })
  })
})

describe('LiveViewSession, device and log', () => {
  it('reads the device from the boot banner and the SD card lines', () => {
    const b = bench()
    b.s.start(b.now())
    b.feed(
      'slot flash_offset 0x00100000',
      '**** WW500 MD. (WW500_C02) Built: 01:06:33 Oct 26 2026 ****',
      "\x1b[mGit branch: 'dev' f3ae0faf",
      '\x1b[0;36mWakeup_event = 0x0002, WakeupEvt1 = 0x0000 RTC Timer',
      'Camera: HM0360',
      '### Warm Boot ###',
      'Timer wake',
      "SD card initialised. CWD is '0:/'",
      "'CONFIG.TXT' found. (Next image #97), camera enabled. Flash brightness 5",
      "Retaining existing images directory (19 <= 100) '/MEDIA/00000000/IMAGES.000'",
      'Wrote 16338 byte image to SD: 59200870.JPG at 0:0:7 1/1/2024',
    )
    const view = b.view()
    expect(view.device).toEqual({
      board: 'WW500_C02', built: '01:06:33 Oct 26 2026', branch: 'dev f3ae0faf', version: null, camera: 'HM0360',
      boot: 'warm', wakeReason: 'timer', flashOffset: '0x00100000', sd: 'ready', sdUsed: null, nextImage: 98,
      imagesDir: '/MEDIA/00000000/IMAGES.000',
    })
    expect(view.log.some(l => /saved 59200870\.JPG to its SD card/.test(l.text))).toBe(true)
  })

  it('logs each frame as one short line', () => {
    const b = streaming()
    b.feed(frameLine([0, 1]))
    expect(b.view().log.at(-1)?.text).toBe('frame 3, 640x480, 0.0 KB, motion 2, threshold 4 (B 4), no wake')
  })

  it('keeps the whole session for a saved log, and the latest lines for the pane', () => {
    const b = streaming()
    for (let i = 0; i < 400; i++) b.feed(`line ${i}`)
    const view = b.view()
    expect(view.log).toHaveLength(300)
    expect(view.logLines).toBe(b.s.fullLog().length)
    expect(b.s.fullLog().some(l => l.text === 'line 0')).toBe(true)
  })
})

describe('logText', () => {
  it('puts the time before each line', () => {
    const at = new Date(2026, 9, 26, 9, 5, 7, 42).getTime()
    expect(logText([{ at, text: 'OpParam 8 = 1000' }])).toBe('09:05:07.042 OpParam 8 = 1000')
  })
})
