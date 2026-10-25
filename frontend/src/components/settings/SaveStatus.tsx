// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The saved or not-saved line every settings panel shows (hooks/useSaveStatus.ts).
import { Check, CircleAlert } from 'lucide-react'
import type { SaveState } from '../../hooks/useSaveStatus'

export function SaveStatus({ status }: { status: SaveState | null }) {
  if (!status) return null
  const Icon = status.ok ? Check : CircleAlert
  return (
    <div
      role="status"
      style={{
        display: 'flex', gap: '0.35rem', alignItems: 'center', fontSize: '0.8125rem',
        color: status.ok ? 'var(--success, #10b981)' : 'var(--error, #ef4444)',
      }}
    >
      <Icon size={14} aria-hidden style={{ flexShrink: 0 }} />
      <span>{status.text}</span>
    </div>
  )
}
