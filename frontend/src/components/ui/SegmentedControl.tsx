/**
 * SegmentedControl: one choice from a few options shown side by side (a view, a period, a
 * chart kind). The shared primitive the UI audit asks for (README, "One chrome": one segmented
 * control); pages adopt it in place of their own button rows as they are next changed.
 *
 *   <SegmentedControl
 *     ariaLabel="View"
 *     value={view}
 *     onChange={setView}
 *     options={[{ value: 'grid', label: 'Grid', icon: <LayoutGrid size={14} /> }, { value: 'list', label: 'List' }]}
 *   />
 */
import type { ReactNode } from 'react'

export interface SegmentOption<T extends string> {
  value: T
  label: ReactNode
  /** A lucide icon element shown before the label. */
  icon?: ReactNode
  /** Tooltip, and the accessible name when the label is only an icon. */
  title?: string
  disabled?: boolean
}

export function SegmentedControl<T extends string>({ options, value, onChange, ariaLabel, size = 'sm' }: {
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  /** What the choice is about, for screen readers. */
  ariaLabel: string
  size?: 'sm' | 'md'
}) {
  const pad = size === 'sm' ? '0.25rem 0.625rem' : '0.4rem 0.875rem'
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      style={{
        display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
        overflow: 'hidden', background: 'var(--bg-color)', flexShrink: 0,
      }}
    >
      {options.map((o, i) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={o.title}
            title={o.title}
            disabled={o.disabled}
            onClick={() => { if (!on) onChange(o.value) }}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.3rem', padding: pad,
              fontSize: size === 'sm' ? '0.75rem' : '0.8125rem', fontWeight: on ? 600 : 500,
              border: 'none', borderLeft: i > 0 ? '1px solid var(--border)' : 'none',
              background: on ? 'var(--primary)' : 'transparent',
              color: on ? '#fff' : 'var(--text-muted)',
              cursor: o.disabled ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
            }}
          >
            {o.icon}
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
