// Shared styles of the live view's parts (#400).
import type { CSSProperties } from 'react'

export const INPUT: CSSProperties = {
  width: '7rem', padding: '0.375rem 0.5rem', fontSize: '0.875rem', borderRadius: 'var(--radius)',
  border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)',
}

export const MONO: CSSProperties = { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace' }

export const SECTION: CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.5rem' }

export const SECTION_TITLE: CSSProperties = {
  margin: 0, fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em',
  color: 'var(--text-muted)',
}

export const ROW: CSSProperties = { display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }

export const CHECK: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }
