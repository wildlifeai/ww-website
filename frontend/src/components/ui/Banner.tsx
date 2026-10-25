// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Banner: the one notice strip for a page (UI audit 02 #28). A tone sets the colour and the
// default icon (DESIGN.md semantic colours); an action sits on the right and `onClose` adds a
// dismiss button. Anything below the message (a progress bar) goes in `footer`.
import type { ReactNode } from 'react'
import { CircleCheck, CircleAlert, Info, TriangleAlert, X, type LucideIcon } from 'lucide-react'

export type BannerTone = 'info' | 'success' | 'warning' | 'error'

const TONES: Record<BannerTone, { rgb: string; icon: LucideIcon }> = {
  info: { rgb: '59,130,246', icon: Info },
  success: { rgb: '16,185,129', icon: CircleCheck },
  warning: { rgb: '245,158,11', icon: TriangleAlert },
  error: { rgb: '239,68,68', icon: CircleAlert },
}

interface Props {
  tone: BannerTone
  children: ReactNode
  /** Replaces the tone's icon. */
  icon?: LucideIcon
  /** A link or button at the right. */
  action?: ReactNode
  onClose?: () => void
  footer?: ReactNode
}

export function Banner({ tone, children, icon, action, onClose, footer }: Props) {
  const t = TONES[tone]
  const Icon = icon ?? t.icon
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      style={{
        display: 'flex', flexDirection: 'column', gap: '0.4rem',
        margin: '0 0 0.75rem', padding: '0.5rem 0.75rem', fontSize: '0.8125rem',
        border: `1px solid rgba(${t.rgb},0.35)`, borderRadius: 'var(--radius)',
        backgroundColor: `rgba(${t.rgb},0.08)`, color: 'var(--text-color)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <Icon size={16} aria-hidden style={{ flexShrink: 0, color: `rgb(${t.rgb})` }} />
        <span style={{ flex: 1, minWidth: 0 }}>{children}</span>
        {action}
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Dismiss"
            style={{ display: 'flex', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-muted)' }}
          >
            <X size={14} aria-hidden />
          </button>
        )}
      </div>
      {footer}
    </div>
  )
}

/** A thin progress track for a banner footer; `pct` is 0 to 100. */
export function BannerProgress({ pct, tone = 'info' }: { pct: number; tone?: BannerTone }) {
  return (
    <div style={{ height: 4, borderRadius: 2, background: `rgba(${TONES[tone].rgb},0.2)`, overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${pct}%`, background: `rgb(${TONES[tone].rgb})`, borderRadius: 2, transition: 'width 0.4s ease' }} />
    </div>
  )
}
