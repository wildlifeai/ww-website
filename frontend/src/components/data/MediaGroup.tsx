import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

// ─────────────────────────────────────────────────────────────────────────────
// MediaGroup: collapsible section that wraps a group of thumbnail cards.
//
// Used by MediaBrowser when a "group by" mode is active. Each group gets a
// header with the group name, item count, and a collapse toggle. The grid owns
// the open state, so it can collapse or expand every group at once.
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  /** Display label for the group (e.g. species name, deployment name). */
  label: string
  /** Number of items in this group. */
  count: number
  open: boolean
  onToggle: () => void
  /** Optional interactive controls rendered at the right of the header, outside
   *  the collapse toggle (e.g. select the group, or a per-cluster "label all"). */
  action?: ReactNode
  /** The thumbnail grid or content to render inside the group. */
  children: ReactNode
}

export function MediaGroup({ label, count, open, onToggle, action, children }: Props) {
  return (
    <div style={{ marginBottom: '0.75rem' }}>
      {/* Header row: collapse toggle (button) + optional action beside it */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          flex: 1,
          minWidth: 0,
          padding: '0.5rem 0.75rem',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          backgroundColor: 'rgba(255,255,255,0.03)',
          color: 'var(--text-color)',
          cursor: 'pointer',
          fontSize: '0.8125rem',
          fontWeight: 600,
          textAlign: 'left',
          transition: 'background-color 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.06)')}
        onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.03)')}
      >
        {/* Collapse indicator */}
        <ChevronRight
          size={14}
          aria-hidden
          style={{ flexShrink: 0, color: 'var(--text-muted)', transition: 'transform 0.2s', transform: open ? 'rotate(90deg)' : 'none' }}
        />

        {/* Label */}
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {label}
        </span>

        {/* Count */}
        <span style={{
          fontSize: '0.75rem',
          fontWeight: 400,
          color: 'var(--text-muted)',
          flexShrink: 0,
        }}>
          {count} {count === 1 ? 'photo' : 'photos'}
        </span>
      </button>

      {/* Action (outside the toggle button so its inputs/buttons work) */}
      {action && <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>{action}</div>}
      </div>

      {/* Content (thumbnail grid) */}
      {open && (
        <div style={{ marginTop: '0.5rem', paddingLeft: '0.25rem' }}>
          {children}
        </div>
      )}
    </div>
  )
}
