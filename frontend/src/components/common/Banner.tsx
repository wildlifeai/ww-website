/**
 * Banner: the one notice style above a page (UI audit 01 A5). The demo notice, pending
 * invitations and the quota warning all render as Banners in the Layout's `BannerSlot`, so they
 * stack in one place, one style, instead of three colours and three icon sets.
 */
import type { ReactNode } from 'react'
import { X, type LucideIcon } from 'lucide-react'

const TONE = { info: 'var(--primary)', warning: 'var(--warning)' } as const

export function Banner({ icon: Icon, tone = 'info', children, actions, onDismiss }: {
  icon: LucideIcon
  tone?: keyof typeof TONE
  children: ReactNode
  /** Buttons at the end of the line. */
  actions?: ReactNode
  onDismiss?: () => void
}) {
  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap',
        padding: '0.55rem 0.9rem', fontSize: '0.8125rem',
        border: '1px solid var(--border)', borderLeft: `4px solid ${TONE[tone]}`,
        borderRadius: 'var(--radius)', background: 'var(--surface)',
      }}
    >
      <Icon size={16} color={TONE[tone]} aria-hidden="true" style={{ flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: '14rem' }}>{children}</div>
      {actions}
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          title="Dismiss"
          style={{ display: 'flex', padding: 2, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}
        >
          <X size={15} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

/** Where banners stack above the page; it takes no space when none shows. */
export function BannerSlot({ children }: { children: ReactNode }) {
  return <div className="banner-slot">{children}</div>
}
