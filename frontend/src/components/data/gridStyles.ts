// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Inline styles shared by the review grid's parts (MediaBrowser and the Grid* components).
import type { CSSProperties } from 'react'

export const SMALL_BTN: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.72rem', padding: '0.2rem 0.5rem',
  border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent',
  color: 'var(--primary)', cursor: 'pointer', whiteSpace: 'nowrap', textDecoration: 'none',
}

// A thin divider that separates the purpose-groups in the ribbon's first row
// (pager │ display controls │ selection).
export const RIBBON_SECTION: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  borderLeft: '1px solid var(--border)', paddingLeft: '0.7rem', marginLeft: '0.7rem',
}

export const ELLIPSIS: CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }
export const TWO_LINES: CSSProperties = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }

export const pagerButton = (disabled: boolean): CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', padding: '0.15rem', border: '1px solid var(--border)',
  borderRadius: 'var(--radius)', background: 'transparent', cursor: disabled ? 'not-allowed' : 'pointer',
  color: disabled ? 'var(--text-muted)' : 'var(--primary)',
})

export const cardStyle = (sel: boolean, isOpen: boolean): CSSProperties => ({
  border: sel || isOpen ? '2px solid var(--primary)' : '1px solid var(--border)',
  borderRadius: 'var(--radius)', overflow: 'hidden', cursor: 'pointer',
  backgroundColor: 'var(--surface)', transition: 'border-color 0.15s, transform 0.15s',
  transform: isOpen ? 'scale(1.02)' : undefined, userSelect: 'none',
})

// Every card keeps a 4:3 image area whatever the column width (02 #20).
export const IMAGE_AREA: CSSProperties = {
  aspectRatio: '4 / 3', display: 'flex', alignItems: 'center', justifyContent: 'center',
  overflow: 'hidden', position: 'relative',
}

/** The cards' grid: as many columns of at least `thumbScale` px as fit. */
export const gridStyle = (thumbScale: number, compact: boolean): CSSProperties => ({
  display: 'grid',
  gridTemplateColumns: `repeat(auto-fill, minmax(${thumbScale}px, 1fr))`,
  gap: compact ? '0.5rem' : '0.75rem',
})
