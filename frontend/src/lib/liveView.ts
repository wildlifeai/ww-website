/**
 * The WW500 live preview over the AI board's USB serial console (#400): command typing, frame
 * lines, the HM0360 motion grid and the wake rule.
 *
 * The contract is the firmware's `_Documentation/live_preview.md` (Seeed_Grove_Vision_AI_Module_V2
 * dev f3ae0faf, unchanged since e3c4d18a), which Charles's `_Tools/live_view.py` also follows.
 * Change it there first, then here.
 * The console runs at 921600 baud, keeps one received character at a time (so commands are typed
 * one character every 30 ms) and runs a command on "\n". Each preview frame is one JSON line:
 * `{"type": 1, "name": "INVOKE", ..., "data": {"count", "resolution", "md", "mdstat", "image"}}`.
 */

export const BAUD_RATE = 921600
export const CHAR_DELAY_MS = 30
/** A VGA frame line is 35 to 70 KB; a longer run with no newline is noise (live_view.py MAX_LINE). */
const MAX_LINE = 512 * 1024

export const MD_COLS = 16
export const MD_ROWS = 16
const MD_BYTES = 32

/** The HM0360 sleep register stops at 0xffff * 1000 / 0x8030 ms (calculateSleepTime()). */
export const HM0360_MAX_INTERVAL_MS = Math.floor((0xffff * 1000) / 0x8030)

/** The three motion op params the page reads and writes, with the ranges the firmware accepts. */
export const OP_SENSITIVITY = 17
export const OP_THRESHOLD = 33
export const OP_MD_INTERVAL = 11
export type MotionOp = typeof OP_SENSITIVITY | typeof OP_THRESHOLD | typeof OP_MD_INTERVAL
export const OP_RANGE: Record<MotionOp, [min: number, max: number]> = {
  [OP_SENSITIVITY]: [1, 3],
  [OP_THRESHOLD]: [1, 255],
  [OP_MD_INTERVAL]: [0, HM0360_MAX_INTERVAL_MS],
}

export function validOpValue(op: MotionOp, value: number): boolean {
  const [min, max] = OP_RANGE[op]
  return Number.isInteger(value) && value >= min && value <= max
}

/** Types `text` one byte at a time, CHAR_DELAY_MS apart: the console keeps one character. */
export async function typeSlowly(
  text: string,
  write: (byte: Uint8Array) => Promise<void>,
  sleep: (ms: number) => Promise<void>,
): Promise<void> {
  for (const byte of new TextEncoder().encode(text)) {
    await write(Uint8Array.of(byte))
    await sleep(CHAR_DELAY_MS)
  }
}

/** Splits the byte stream into lines on "\n", dropping a run longer than MAX_LINE. */
export class LineSplitter {
  private parts: Uint8Array[] = []
  private size = 0
  overflows = 0

  push(chunk: Uint8Array): Uint8Array[] {
    const lines: Uint8Array[] = []
    let start = 0
    for (let nl = chunk.indexOf(10); nl >= 0; nl = chunk.indexOf(10, start)) {
      lines.push(this.take(chunk.subarray(start, nl)))
      start = nl + 1
    }
    if (start < chunk.length) {
      this.parts.push(chunk.slice(start))
      this.size += chunk.length - start
      if (this.size > MAX_LINE) {
        this.parts = []
        this.size = 0
        this.overflows++
      }
    }
    return lines
  }

  private take(tail: Uint8Array): Uint8Array {
    if (!this.parts.length) return tail.slice()
    const line = new Uint8Array(this.size + tail.length)
    let at = 0
    for (const part of [...this.parts, tail]) {
      line.set(part, at)
      at += part.length
    }
    this.parts = []
    this.size = 0
    return line
  }
}

// eslint-disable-next-line no-control-regex -- the console colours some lines with ANSI escapes
const ANSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g

/** A console line without colour codes or the surrounding CR, LF, spaces and tabs. */
export function cleanLine(line: string): string {
  return line.replace(ANSI_RE, '').replace(/^[\r\n \t]+|[\r\n \t]+$/g, '')
}

/** The HM0360 wake registers sent with a frame ("mdstat"). thA is MD_BLOCK_NUM_TH of context A,
 *  the preview frames; thB is context B, the frames it checks while asleep. */
export interface MdStat {
  int: number
  ctrl: number
  ctrl1: number
  thA: number
  thB: number
}

export interface PreviewFrame {
  count: number
  width: number
  height: number
  /** The JPEG, base64 as sent. */
  image: string
  /** The 32 ROI_OUT motion bytes, or null when the frame has none (no HM0360, older firmware). */
  md: Uint8Array | null
  mdstat: MdStat | null
}

const FRAME_MARKER = '"name": "INVOKE"'
const BASE64_RE = /^[A-Za-z0-9+/]*={0,2}$/
const HEX64_RE = /^[0-9a-fA-F]{64}$/

function int(value: unknown): number | null {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value
  return typeof n === 'number' && Number.isInteger(n) ? n : null
}

function parseMdStat(value: unknown): MdStat | null {
  if (typeof value !== 'object' || value === null) return null
  const v = value as Record<string, unknown>
  const stat = { int: int(v.int), ctrl: int(v.ctrl), ctrl1: int(v.ctrl1), thA: int(v.thA), thB: int(v.thB) }
  return Object.values(stat).every(n => n !== null) ? (stat as MdStat) : null
}

function parseMd(value: unknown): Uint8Array | null {
  if (typeof value !== 'string' || !HEX64_RE.test(value)) return null
  const bytes = new Uint8Array(MD_BYTES)
  for (let i = 0; i < MD_BYTES; i++) bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16)
  return bytes
}

/**
 * The frame on a console line, 'damaged' for a frame line that does not parse (a print from
 * another firmware task can land inside one), or null for any other line.
 */
export function parseFrameLine(line: string): PreviewFrame | 'damaged' | null {
  if (!line.includes(FRAME_MARKER)) return null
  const start = line.indexOf('{"type"')
  if (start < 0) return null
  try {
    const data = (JSON.parse(line.slice(start)) as { data?: Record<string, unknown> }).data
    const image = data?.image
    if (typeof image !== 'string' || !image || image.length % 4 !== 0 || !BASE64_RE.test(image)) return 'damaged'
    const res = Array.isArray(data?.resolution) ? data.resolution : []
    return {
      count: int(data?.count) ?? 0,
      width: int(res[0]) ?? 0,
      height: int(res[1]) ?? 0,
      image,
      md: parseMd(data?.md),
      mdstat: parseMdStat(data?.mdstat),
    }
  } catch {
    return 'damaged'
  }
}

/**
 * The cells with motion, as row * 16 + column in picture order (row 0 at the top).
 *
 * Bit b is byte b >> 3, bit b & 7 (LSB first): console grid row b >> 4, column b & 15, the order
 * of hm0360_md_printGrid(). That grid is the picture turned 180 degrees (bench, 23 and 24 Oct
 * 2026), so bit b is picture cell 255 - b. All 16 rows show motion on the bench, although the
 * datasheet gives 15 for VGA.
 */
export function motionCells(md: Uint8Array): number[] {
  const last = MD_COLS * MD_ROWS - 1
  const cells: number[] = []
  for (let b = last; b >= 0; b--) {
    if (md[b >> 3] & (1 << (b & 7))) cells.push(last - b)
  }
  return cells
}

/** The HM0360 raises its motion flag, which wakes the camera, when MORE cells than the
 *  threshold show motion (MD application note 3.1; on the bench the interrupt came on at exactly
 *  the first such frame, 24 Oct 2026). */
export function wouldWake(cells: number, threshold: number): boolean {
  return cells > threshold
}

/**
 * The capture interval for the stream. The HM0360 spaces its frames by the motion interval
 * (op 11), so motion is compared across frames as far apart as in the field, capped at what its
 * sleep register holds, or 1000 ms when op 11 is 0 or not known yet. The RP3 (and RP2) take
 * 100 ms. With motion detection off it is plain live video: 20 ms on the HM0360, 100 ms on the
 * RP3 (live_view.py). Never 0: FreeRTOS asserts on a zero timer period.
 */
export function captureIntervalMs(camera: string | null, op11: number | null, motionDetection = true): number {
  if (camera === 'HM0360') {
    if (!motionDetection) return MIN_FRAME_INTERVAL_MS
    return op11 ? Math.min(HM0360_MAX_INTERVAL_MS, Math.max(MIN_FRAME_INTERVAL_MS, op11)) : 1000
  }
  if (camera === 'RP3' || camera === 'RP2') return 100
  return 500
}

/** The frame interval box's range: 20 ms is live_view.py's minimum. */
export const MIN_FRAME_INTERVAL_MS = 20
export const MAX_FRAME_INTERVAL_MS = 60000

export function validFrameInterval(ms: number): boolean {
  return Number.isInteger(ms) && ms >= MIN_FRAME_INTERVAL_MS && ms <= MAX_FRAME_INTERVAL_MS
}

/** Grid rows: 16 is what the bench shows; 15 is what the datasheet gives for VGA (live_preview.md). */
export type GridRows = 15 | 16

/**
 * The cells to draw for a grid of `rows` rows, from the 16-row cells motionCells() returns, and
 * how many set bits fall beyond the last row. With 15 rows the console's row 15 is left out and
 * the 180 degree turn is within 15 rows, as live_view.py's md_cells() does.
 */
export function gridCells(cells16: readonly number[], rows: GridRows): { cells: { row: number; col: number }[]; outside: number } {
  const last = MD_COLS * MD_ROWS - 1
  const cells: { row: number; col: number }[] = []
  let outside = 0
  for (const cell of cells16) {
    const bit = last - cell
    const consoleRow = bit >> 4
    if (consoleRow >= rows) {
      outside++
      continue
    }
    cells.push({ row: rows - 1 - consoleRow, col: MD_COLS - 1 - (bit & 15) })
  }
  return { cells, outside }
}

/** live_view.py's quick command buttons, in its order. */
export const QUICK_COMMANDS = ['getop 27', 'setop 27 286', 'setop 28 326', 'vcm 512', 'status', 'setop 8 1000'] as const

/** The reply lines that answer a status query or an op write (live_view.py reply_expected()). */
export function replyExpected(cmd: string): string[] {
  const [verb, a] = cmd.split(' ')
  if (verb === 'getop') return [`OpParam ${a} =`]
  if (verb === 'setop') return [`Set OpParam ${a} =`, `valid value for op ${a}.`]
  if (verb === 'camera') return ['HM0360', 'RP3', 'RP2']
  if (verb === 'interval') return ['ms from the next image', 'No capture in progress']
  return []
}

/** Console lines the session acts on, as the firmware prints them at e3c4d18a. */
export const LINES = {
  op: /OpParam (\d+) = (\d+)/,
  camera: /^(?:cmd> )?(HM0360|RP3|RP2)$|initialised for camera (HM0360|RP3|RP2)/,
  coldBoot: /### Cold Boot ###/,
  warmBoot: /### Warm Boot ###/,
  cliUp: /Starting CLI Task|Enter 'help' to view/,
  /** Camera and data path ready; a capture before this times out. */
  bootDone: /Image sensor and data path initialised/,
  captureOk: /About to capture/,
  burstStart: /Images to capture: \d+/,
  burstEnd: /Current captures completed:|Capture failed after/,
  /** captureRefused(): between a cold boot and the first sleep. */
  refused: /no captures until after the first sleep/,
  previewOn: /Preview mode 1/,
  previewOff: /Preview off/,
  enteringDpd: />>> Entering DPD/,
  /** A capture the camera saved to its card, which preview prevents. */
  sdWrite: /Wrote \d+ byte image to SD: (\S+)/,
}

/** Boot banner and console lines the Device tab reads, as the firmware prints them on dev f3ae0faf. */
export const DEVICE_LINES = {
  /** `**** WW500 MD. (WW500_C02) Built: 01:06:33 Oct 26 2026 ****` */
  build: /\*{4} WW500 \S+ \(([^)]+)\) Built: (.+?) \*{4}/,
  /** `Git branch: 'dev' f3ae0faf` */
  branch: /Git branch: '([^']+)' (\S+)/,
  camera: /^Camera: (\S+)/,
  /** `Wakeup_event = 0x0002, WakeupEvt1 = 0x0000 RTC Timer` */
  wakeReason: /Wakeup_event = 0x[0-9a-fA-F]+, WakeupEvt1 = 0x[0-9a-fA-F]+ (.+)$/,
  /** The second-stage bootloader names the slot it starts from by its flash offset. */
  flashOffset: /slot flash_offset (0x[0-9a-fA-F]+)/,
  sdReady: /SD card initialised/,
  sdFailed: /SD card initialisation failed/,
  nextImage: /'CONFIG\.TXT' found\. \(Next image #(\d+)\)/,
  imagesDir: /images directory .*'([^']+)'/,
}
