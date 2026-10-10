// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The one-line state of a CamtrapDP export under its Download button (#328).
import type { CSSProperties } from 'react'
import { exportStatus } from '../../lib/camtrapExport'
import type { useCamtrapExport } from '../../hooks/useCamtrapExport'

const COLOR = { progress: 'inherit', done: 'var(--success, #4caf50)', warning: 'var(--warning, #ff9800)', error: 'var(--error, #f44336)' }
const ICON = { progress: '⏳', done: '✓', warning: '⚠', error: '⚠' }

export function CamtrapExportStatus({ state, style: extra }: { state: ReturnType<typeof useCamtrapExport>; style?: CSSProperties }) {
  const style = { fontSize: '0.8125rem', margin: 0, ...extra }
  if (state.error) return <p style={{ ...style, color: COLOR.error }}>{ICON.error} {state.error}</p>
  if (state.downloaded) return <p style={{ ...style, color: COLOR.done }}>✓ Download started.</p>
  if (!state.job) return null
  const { tone, text } = exportStatus(state.job)
  const link = (tone === 'done' || tone === 'warning') && state.job.result_url
  return (
    <p style={{ ...style, color: COLOR[tone], opacity: tone === 'progress' ? 0.75 : 1 }}>
      {ICON[tone]} {text}
      {link && <> <a href={link} style={{ color: 'var(--primary)' }}>Download again</a></>}
    </p>
  )
}
