// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The live view's downloads (#400): a frame as the camera sent it, the same frame with the motion
// grid drawn on it, and the session log. Names carry the local time so a bench run sorts in order.

/** `2026-10-26T103015`: local time, safe in a file name. */
export function fileStamp(date: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`
}

export function frameFileName(date: Date, frameCount: number, withGrid: boolean): string {
  return `ww500-frame-${fileStamp(date)}-${frameCount}${withGrid ? '-grid.png' : '.jpg'}`
}

export function logFileName(date: Date): string {
  return `ww500-live-view-${fileStamp(date)}.txt`
}

/** The JPEG bytes of a frame's base64 image. */
export function jpegBytes(base64: string): Uint8Array<ArrayBuffer> {
  const raw = atob(base64)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

/** Hands the browser a file to save. */
export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.rel = 'noopener'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
