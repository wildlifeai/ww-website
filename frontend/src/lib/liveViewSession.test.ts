import { describe, expect, it } from 'vitest'
import { LiveViewSession, liveViewStatus, logText } from './liveViewSession'

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
  'setop 7 10': 'Set OpParam 7 = 10',
  'setop 7 0': 'Set OpParam 7 = 0',
  'setop 8 1000': 'Set OpParam 8 = 1000',
  'preview 1': "Preview mode 1 (stream, no SD saves). Start frames with e.g. 'capture 1000 0'.",
  'preview 0': 'Preview off.',
  'capture 10 500': "About to capture 10 images with an interval of '500' milliseconds",
  'capture 10 1000': "About to capture 10 images with an interval of '1000' milliseconds",
  dpd: 'Forcing DPD by clearing inactivity period',
}

const CONNECT = ['ver', 'getop 7', 'getop 8', 'setop 8 60000', 'camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33']

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
  }
  const take = () => sent.splice(0)
  return { s, feed, run, take, now: () => t }
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
    expect(b.s.snapshot()).toMatchObject({ phase: 'starting', camera: 'HM0360', ops: { 10: 1, 11: 500, 17: 2, 33: 4 } })
  })

  it('shows each frame with its motion cells and wake verdict', () => {
    const b = streaming()
    expect(b.s.snapshot().frame).toMatchObject({ image: JPEG_B64, cells: [], threshold: 4, wake: false, width: 640 })
    b.feed(frameLine([0, 1, 2, 3, 4]))
    expect(b.s.snapshot()).toMatchObject({ phase: 'streaming', frame: { cells: [251, 252, 253, 254, 255], wake: true } })
  })

  it('counts a damaged frame and keeps the last good picture', () => {
    const b = streaming()
    b.feed('{"type": 1, "name": "INVOKE", "data": {"image": "/9j/[IF] sent"}}')
    expect(b.s.snapshot()).toMatchObject({ dropped: 1, frame: { image: JPEG_B64 } })
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

  it('after the wake burst, reads the settings at once rather than 3 s after its last frame', () => {
    const b = bench(AWAKE, { replyFirst: true })
    b.s.start(b.now())
    b.feed('### Warm Boot ###', 'Starting CLI Task', 'Images to capture: 3')
    b.run(200)
    b.feed(frameLine(), 'Current captures completed: 3')
    // Ten steps of about 0.35 s each; waiting 3 s after the last frame for each took 27 s.
    b.run(4500)
    expect(b.take()).toEqual(['preview 1', 'getop 7', 'getop 8', 'setop 8 60000', 'camera', 'getop 10', 'getop 11',
      'getop 17', 'getop 33', 'capture 10 500'])
  })

  it('says the camera is probably asleep when nothing answers', () => {
    const b = bench({})
    b.s.start(b.now())
    b.run(12000)
    const view = b.s.snapshot()
    expect(view.silent).toBe(true)
    expect(liveViewStatus(view)).toMatch(/probably asleep/)
  })
})

describe('LiveViewSession, the first sleep', () => {
  it('after a cold boot sets a 10 s wake, sends it to sleep, and streams when it wakes', () => {
    const b = bench()
    b.s.start(b.now())
    // Opening the port reset the board.
    b.feed('### Cold Boot ###', 'Starting CLI Task')
    b.run(2000)
    expect(b.take()).toEqual(['getop 7', 'getop 8', 'setop 7 10'])
    b.feed('Image sensor and data path initialised for camera HM0360')
    b.run(5000)
    expect(b.take()).toEqual(['dpd'])
    expect(b.s.snapshot().phase).toBe('sleeping')
    b.run(10000)
    expect(b.take()).toEqual([]) // no keep-awake while it must sleep

    b.feed('### Warm Boot ###', 'Starting CLI Task')
    b.run(3000)
    expect(b.take()).toEqual(['preview 1', 'setop 8 60000', 'camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33'])
    b.feed('Image sensor and data path initialised for camera HM0360')
    b.run(5000)
    expect(b.take()).toEqual(['preview 1', 'capture 10 500'])
  })

  it('when the camera refuses a capture, sends it to sleep with the wake set', () => {
    const b = bench({ ...AWAKE, 'capture 10 500': 'Error: no captures until after the first sleep.' })
    b.s.start(b.now())
    b.run(10000)
    expect(b.take()).toEqual([...CONNECT, 'preview 1', 'capture 10 500', 'setop 7 10', 'dpd'])
    expect(liveViewStatus(b.s.snapshot())).toMatch(/wakes in about 10 s/)
  })
})

describe('LiveViewSession, settings', () => {
  it('writes an op, then reads it back so a refused value does not stay', () => {
    const b = streaming()
    b.s.setOp(33, 6, b.now())
    expect(b.s.snapshot().ops[33]).toBe(6)
    // One step after each frame: the console is free between frames.
    b.run(500)
    b.feed('Error: 6 is not a valid value for op 33.')
    b.feed(frameLine())
    b.run(500)
    b.feed('OpParam 33 = 4')
    expect(b.take()).toEqual(['setop 33 6', 'getop 33'])
    expect(b.s.snapshot().ops[33]).toBe(4)
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
})

describe('LiveViewSession, stopping', () => {
  it('waits for the burst, then turns preview off, puts op 8 back and sends it to sleep', () => {
    const b = streaming()
    b.s.disconnect(b.now())
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(liveViewStatus(b.s.snapshot())).toMatch(/Disconnecting/)
    b.feed('Current captures completed: 10')
    b.run(2000)
    expect(b.take()).toEqual(['preview 0', 'setop 8 1000', 'dpd'])
    const view = b.s.snapshot()
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

  it('puts back op 7 after the first-sleep wake, and quiet values over an earlier session\'s', () => {
    const b = bench({ ...AWAKE, 'getop 7': 'OpParam 7 = 10', 'getop 8': 'OpParam 8 = 60000' })
    b.s.start(b.now())
    b.feed('### Cold Boot ###', 'Starting CLI Task')
    b.run(2000)
    b.s.disconnect(b.now())
    b.run(3000)
    expect(b.take()).toEqual(['getop 7', 'getop 8', 'setop 7 10', 'preview 0', 'setop 7 0', 'dpd'])
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
    const b = bench({})
    b.s.start(b.now())
    b.run(1000)
    b.s.disconnect(b.now())
    b.run(31000)
    const view = b.s.snapshot()
    // Only ver was typed before Stop, so op 8 was never written and is not put back.
    expect(view.phase).toBe('stopped')
    expect(view.unconfirmed).toEqual(['preview 0', 'dpd'])
    expect(liveViewStatus(view)).toMatch(/did not confirm: preview 0, dpd/)
  })

  it('reports a lost connection', () => {
    const b = streaming()
    b.s.disconnected(b.now())
    expect(liveViewStatus(b.s.snapshot())).toMatch(/connection closed/)
  })
})

describe('LiveViewSession, run modes', () => {
  it('Stop lets the burst finish and arms no more, with preview still on', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10')
    b.run(2000)
    expect(b.take()).toEqual([])
    expect(b.s.snapshot()).toMatchObject({ phase: 'paused', runMode: 'paused' })
    expect(liveViewStatus(b.s.snapshot())).toMatch(/Free run or One image/)
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
    expect(b.s.snapshot().phase).toBe('paused')
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
    expect(b.s.snapshot()).toMatchObject({ intervalMs: 200, intervalSet: true })
    b.feed('Current captures completed: 10')
    b.run(1000)
    expect(b.take()).toEqual(['capture 10 200'])
  })

  it('with motion detection off streams plain video at 20 ms and counts no wakes', () => {
    const b = streaming()
    b.feed(frameLine([0, 1, 2, 3, 4]))
    expect(b.s.snapshot().md).toEqual({ frames: 2, wakes: 1 })
    b.s.setMotionDetection(false, b.now())
    b.feed(frameLine([0, 1, 2, 3, 4]))
    b.run(500)
    expect(b.take()).toEqual(['interval 20'])
    expect(b.s.snapshot()).toMatchObject({ motionDetection: false, intervalMs: 20, intervalSet: false, md: { frames: 0, wakes: 0 } })
  })

  it('with Keep awake off sends no CR, so the camera can sleep', () => {
    const b = bench()
    b.s.start(b.now())
    b.s.setKeepAwake(false, b.now())
    b.run(25000)
    expect(b.take().filter(c => c === 'CR')).toHaveLength(0)
  })

  it('after sleeping on its own and waking, starts Free run again', () => {
    const b = streaming()
    b.s.pause(b.now())
    b.feed('Current captures completed: 10', '>>> Entering DPD at 2024:01:01 00:00:12')
    expect(b.s.snapshot()).toMatchObject({ phase: 'sleeping', sleepReason: 'idle' })
    expect(liveViewStatus(b.s.snapshot())).toMatch(/Motion or its timer wakes it/)
    b.run(11000)
    expect(b.take()).toEqual([]) // nothing typed while it sleeps
    b.feed('### Warm Boot ###', 'Starting CLI Task')
    b.run(3000)
    expect(b.take()).toEqual(['preview 1', 'setop 8 60000', 'camera', 'getop 10', 'getop 11', 'getop 17', 'getop 33'])
    b.feed('Image sensor and data path initialised for camera HM0360')
    b.run(5000)
    expect(b.take()).toEqual(['preview 1', 'capture 10 500'])
    expect(b.s.snapshot().runMode).toBe('free')
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
      "SD card initialised. CWD is '0:/'",
      "'CONFIG.TXT' found. (Next image #97), camera enabled. Flash brightness 5",
      "Retaining existing images directory (19 <= 100) '/MEDIA/00000000/IMAGES.000'",
      'Wrote 16338 byte image to SD: 59200870.JPG at 0:0:7 1/1/2024',
    )
    const view = b.s.snapshot()
    expect(view.device).toEqual({
      board: 'WW500_C02', built: '01:06:33 Oct 26 2026', branch: 'dev f3ae0faf',
      version: 'WW500 MD. (WW500_C02) Built: 01:06:33 Oct 26 2026', camera: 'HM0360', boot: 'warm',
      wakeReason: 'RTC Timer', flashOffset: '0x00100000', sd: 'ready', nextImage: 98, imagesDir: '/MEDIA/00000000/IMAGES.000',
    })
    expect(view.log.some(l => /saved 59200870\.JPG to its SD card/.test(l.text))).toBe(true)
  })

  it('logs each frame as one short line', () => {
    const b = streaming()
    b.feed(frameLine([0, 1]))
    expect(b.s.snapshot().log.at(-1)?.text).toBe('frame 3, 640x480, 0.0 KB, motion 2, threshold 4 (B 4), no wake')
  })

  it('keeps the whole session for a saved log, and the latest lines for the pane', () => {
    const b = streaming()
    for (let i = 0; i < 400; i++) b.feed(`line ${i}`)
    const view = b.s.snapshot()
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
