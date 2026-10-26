// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// PageHelp: a "?" beside a page heading that opens a short summary of the page with a link to its
// guide, so the page itself carries no explanation (UI audit, "Chrome" rule). Escape or a click
// outside closes it.

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CircleHelp } from 'lucide-react'

interface PageHelpProps {
  /** What the button is for, read by screen readers, e.g. "About settings". */
  label: string
  /** One or two plain sentences about the page. */
  children: ReactNode
  /** The guide's route, e.g. /guides/project-settings. */
  guide: string
  guideLabel?: string
}

export function PageHelp({ label, children, guide, guideLabel = 'Read the guide' }: PageHelpProps) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <span ref={wrapRef} style={{ position: 'relative', display: 'inline-flex', verticalAlign: 'middle' }}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '0.125rem',
          background: 'none', border: 'none', borderRadius: '50%', cursor: 'pointer',
          color: open ? 'var(--primary)' : 'var(--text-muted)',
        }}
      >
        <CircleHelp size={18} aria-hidden />
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label={label}
          style={{
            position: 'absolute', left: 0, top: 'calc(100% + 6px)', zIndex: 200, width: 'min(320px, 80vw)',
            padding: '0.75rem 0.875rem', fontSize: '0.875rem', fontWeight: 400, lineHeight: 1.45,
            backgroundColor: 'var(--bg-color)', color: 'var(--text-color)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
          }}
        >
          <div>{children}</div>
          <Link to={guide} onClick={() => setOpen(false)} style={{ display: 'inline-block', marginTop: '0.5rem' }}>
            {guideLabel}
          </Link>
        </div>
      )}
    </span>
  )
}
