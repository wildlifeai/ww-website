/**
 * Read the deployment id a WW500 stamps into every frame's EXIF.
 *
 * The firmware writes the full deployment UUID into a private tag, 0xF200
 * ("Deployment_ID" in backend/app/domain/exif.py), in the TIFF IFD chain of the
 * JPEG's APP1 segment. The card folder (MEDIA/<8-hex>/) carries only a prefix of
 * the same id, and is wrong whenever the folder was created before the id was
 * configured: the bench run of 2026-09-05 had a frame under MEDIA/00000000/ whose
 * EXIF named the real deployment (ww-website#140). So the tag is authoritative
 * and the folder is the fallback.
 *
 * Only the first 128 KB of each file is read: APP1 sits right after SOI, and
 * the parser stops at the first scan marker.
 *
 * The same walk reads Make, so a WW500 test photo (one taken before a
 * deployment was set on the camera) can be told apart from a photo that simply
 * has no EXIF (a BMP frame, another camera); see isTestPhoto.
 */

const MAKE_TAG = 0x010f
const DEPLOYMENT_ID_TAG = 0xf200
const USER_COMMENT_TAG = 0x9286
const EXIF_IFD_POINTER = 0x8769
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/

/** Bytes to read from the head of each file; APP1 is capped at 64 KB by the format. */
export const EXIF_HEAD_BYTES = 128 * 1024

/** The EXIF Make every WW500 writes. */
export const WW500_MAKE = 'Wildlife.ai'

/** The id a camera with no deployment set could carry; treated as no id. */
export const ZERO_DEPLOYMENT_ID = '00000000-0000-0000-0000-000000000000'

/** What the upload needs from a frame's EXIF; both null when the file has none. */
export interface CameraExif {
  /** Deployment UUID, lower-case: Deployment_ID (0xF200), else one found in UserComment. */
  deploymentId: string | null
  /** EXIF Make, trimmed of NULs and spaces. */
  make: string | null
}

const NO_EXIF: CameraExif = { deploymentId: null, make: null }

/**
 * A WW500 photo with no deployment id, or the all-zero one: taken on the bench
 * before a deployment was set on the camera, so it is never uploaded, whatever
 * card folder it sits in (ww-website#287). A file with no EXIF at all is not a
 * test photo; it keeps the folder fallback. Mirrors is_test_photo in
 * backend/app/domain/exif.py.
 */
export function isTestPhoto(exif: CameraExif): boolean {
  if (exif.make?.toLowerCase() !== WW500_MAKE.toLowerCase()) return false
  return !exif.deploymentId || exif.deploymentId === ZERO_DEPLOYMENT_ID
}

function ascii(view: DataView, off: number, len: number): string {
  let s = ''
  for (let i = 0; i < len && off + i < view.byteLength; i++) s += String.fromCharCode(view.getUint8(off + i))
  return s
}

function uuidIn(text: string): string | null {
  // Strip NULs (UserComment carries an 8-byte NUL-padded charset code) before matching.
  const m = text.replace(/\0/g, '').toLowerCase().match(UUID_RE)
  return m ? m[0] : null
}

/**
 * Walk one IFD, following the ExifIFD pointer and the next-IFD link the way the
 * backend parser does. The deployment id is the first UUID found in
 * Deployment_ID, else the first found in UserComment, else null.
 */
function walkIfds(view: DataView, tiffStart: number, firstIfd: number, little: boolean): CameraExif {
  const u16 = (o: number) => view.getUint16(o, little)
  const u32 = (o: number) => view.getUint32(o, little)
  const seen = new Set<number>()
  const queue: number[] = [firstIfd]
  let fromTag: string | null = null
  let fromUserComment: string | null = null
  let make: string | null = null
  const result = (): CameraExif => ({ deploymentId: fromTag ?? fromUserComment, make })

  while (queue.length) {
    const rel = queue.shift() as number
    const ifd = tiffStart + rel
    if (rel === 0 || seen.has(rel) || seen.size > 8 || ifd + 2 > view.byteLength) continue
    seen.add(rel)

    const n = u16(ifd)
    for (let i = 0; i < n; i++) {
      const e = ifd + 2 + i * 12
      if (e + 12 > view.byteLength) return result()
      const tag = u16(e)
      const count = u32(e + 4)
      const valueField = e + 8

      if (tag === EXIF_IFD_POINTER) {
        queue.push(u32(valueField))
        continue
      }
      if (tag !== DEPLOYMENT_ID_TAG && tag !== USER_COMMENT_TAG && tag !== MAKE_TAG) continue

      // ASCII (2) and UNDEFINED (7) are one byte per count; anything else is not
      // a string and cannot hold a uuid or a maker name.
      const type = u16(e + 2)
      if (type !== 2 && type !== 7) continue
      const at = count <= 4 ? valueField : tiffStart + u32(valueField)
      if (at + count > view.byteLength) continue
      const text = ascii(view, at, count)
      if (tag === MAKE_TAG) {
        make = make ?? (text.replace(/\0/g, '').trim() || null)
        continue
      }
      const found = uuidIn(text)
      if (!found) continue
      if (tag === DEPLOYMENT_ID_TAG) fromTag = fromTag ?? found
      else fromUserComment = fromUserComment ?? found
    }

    const next = ifd + 2 + n * 12
    if (next + 4 <= view.byteLength) queue.push(u32(next))
  }
  return result()
}

/** The deployment UUID stamped in a JPEG's EXIF, lower-case, or null when absent. */
export function findDeploymentIdInJpeg(buf: ArrayBuffer): string | null {
  return findCameraExifInJpeg(buf).deploymentId
}

/** The deployment id and Make in a JPEG's EXIF; both null for a non-JPEG or a JPEG without EXIF. */
export function findCameraExifInJpeg(buf: ArrayBuffer): CameraExif {
  const view = new DataView(buf)
  if (view.byteLength < 4 || view.getUint16(0) !== 0xffd8) return NO_EXIF

  let off = 2
  while (off + 4 <= view.byteLength) {
    if (view.getUint8(off) !== 0xff) return NO_EXIF
    const marker = view.getUint8(off + 1)
    // Standalone markers carry no length field.
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      off += 2
      continue
    }
    // Start of scan / end of image: no more metadata segments.
    if (marker === 0xda || marker === 0xd9) return NO_EXIF
    const len = view.getUint16(off + 2)
    if (len < 2) return NO_EXIF
    if (marker === 0xe1 && len >= 10 && ascii(view, off + 4, 6) === 'Exif\0\0') {
      const tiff = off + 10
      if (tiff + 8 > view.byteLength) return NO_EXIF
      const order = ascii(view, tiff, 2)
      const little = order === 'II'
      if (!little && order !== 'MM') return NO_EXIF
      if (view.getUint16(tiff + 2, little) !== 42) return NO_EXIF
      return walkIfds(view, tiff, view.getUint32(tiff + 4, little), little)
    }
    off += 2 + len
  }
  return NO_EXIF
}

/**
 * Deployment id and Make for a batch of files, aligned by index (both null where
 * absent or unreadable). Reads the file heads a few at a time so a whole card
 * does not open every file at once. `onProgress` is called after each file with
 * the number read so far, so a page can show how far through a large card it is.
 */
export async function readCameraExif(
  files: File[],
  concurrency = 8,
  onProgress?: (done: number, total: number) => void,
): Promise<CameraExif[]> {
  const out: CameraExif[] = new Array(files.length).fill(NO_EXIF)
  let next = 0
  let done = 0
  const worker = async () => {
    while (next < files.length) {
      const i = next++
      try {
        const buf = await files[i].slice(0, EXIF_HEAD_BYTES).arrayBuffer()
        out[i] = findCameraExifInJpeg(buf)
      } catch {
        out[i] = NO_EXIF
      }
      onProgress?.(++done, files.length)
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, files.length) }, worker))
  return out
}
