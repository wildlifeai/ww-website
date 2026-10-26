// Save frame (#400): the JPEG as the camera sent it, and on request a PNG with the motion grid and
// the wake verdict drawn on it, for A/B records.
import { MD_COLS, gridCells, type GridRows } from '../../lib/liveView'
import type { LiveViewFrame } from '../../lib/liveViewSession'
import { downloadBlob, frameFileName, jpegBytes } from '../../lib/liveViewFiles'

async function griddedPng(frame: LiveViewFrame, rows: GridRows): Promise<Blob | null> {
  const img = new Image()
  img.src = `data:image/jpeg;base64,${frame.image}`
  await img.decode()
  const canvas = document.createElement('canvas')
  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(img, 0, 0)
  const w = canvas.width / MD_COLS
  const h = canvas.height / rows
  ctx.strokeStyle = 'rgba(0, 170, 255, 0.6)'
  ctx.lineWidth = 1
  ctx.beginPath()
  for (let c = 1; c < MD_COLS; c++) { ctx.moveTo(c * w, 0); ctx.lineTo(c * w, canvas.height) }
  for (let r = 1; r < rows; r++) { ctx.moveTo(0, r * h); ctx.lineTo(canvas.width, r * h) }
  ctx.stroke()
  const grid = frame.cells ? gridCells(frame.cells, rows) : null
  ctx.strokeStyle = 'rgb(255, 40, 40)'
  ctx.lineWidth = 2
  for (const cell of grid?.cells ?? []) ctx.strokeRect(cell.col * w + 1, cell.row * h + 1, w - 2, h - 2)
  if (frame.cells) {
    const caption = `motion ${frame.cells.length}, threshold ${frame.threshold ?? '-'} (B ${frame.thresholdB ?? '-'}), ${frame.wake ? 'WAKE' : 'no wake'}`
    ctx.font = '14px monospace'
    ctx.fillStyle = 'rgba(0, 0, 0, 0.6)'
    ctx.fillRect(0, canvas.height - 22, ctx.measureText(caption).width + 12, 22)
    ctx.fillStyle = frame.wake ? '#ff6b6b' : '#ffffff'
    ctx.fillText(caption, 6, canvas.height - 6)
  }
  return new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
}

/** Downloads the frame, and the gridded copy when asked. */
export async function saveFrame(frame: LiveViewFrame, rows: GridRows, withGrid: boolean): Promise<void> {
  const at = new Date()
  downloadBlob(new Blob([jpegBytes(frame.image)], { type: 'image/jpeg' }), frameFileName(at, frame.count, false))
  if (!withGrid) return
  const png = await griddedPng(frame, rows)
  if (png) downloadBlob(png, frameFileName(at, frame.count, true))
}
