// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// MediaBulkActions: the selection bar for the Annotations grid, shown in the ribbon's
// sub-bar once at least one photo is selected (UI audit 02 #14). The grid only lets people
// select photos in projects they can write to, so everything here is theirs to do.
//  - Label as, Find similar (one photo)
//  - Publish to iNaturalist, or a link to connect it on the Toolkit page
//  - Remove (with confirmation and Undo)
//  - Run AI, and Create species ID model for organisation managers

import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { Bird, ChevronDown, Cpu, Search, Sparkles, Tags, Trash2, type LucideIcon } from 'lucide-react'
import { INAT_CONNECT_PATH } from '../../lib/inatPublish'

export type BulkAction = 'inat' | 'delete' | 'ai' | 'similar' | 'label' | 'train'

interface Props {
  selectedCount: number
  onClearSelection: () => void
  onAction: (action: BulkAction) => void
  inatConnected: boolean
  /** False when iNaturalist is switched off on this server. */
  inatEnabled: boolean
  /** Create species ID model: organisation managers only. */
  canTrain: boolean
}

const item: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  width: '100%', textAlign: 'left', padding: '0.5rem 0.875rem',
  fontSize: '0.8125rem', border: 'none', background: 'transparent',
  color: 'var(--text-color)', cursor: 'pointer', textDecoration: 'none', boxSizing: 'border-box',
}
const hover = {
  onMouseEnter: (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = 'rgba(76,175,80,0.08)'),
  onMouseLeave: (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = 'transparent'),
}
const divider = <div style={{ height: 1, background: 'var(--border)', margin: '0.25rem 0' }} />

export function MediaBulkActions({
  selectedCount,
  onClearSelection,
  onAction,
  inatConnected,
  inatEnabled,
  canTrain,
}: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  // The menu renders in a portal (fixed position) so it isn't clipped by the
  // ribbon's overflow:hidden once this bar lives inside the ribbon.
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null)

  // Close dropdown on outside click (account for the portalled menu).
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  // Reposition on scroll/resize while open (the trigger may move with the page).
  useEffect(() => {
    if (!open) return
    let frame = 0
    const reposition = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const r = btnRef.current?.getBoundingClientRect()
        if (r) setMenuPos({ top: r.bottom + 4, left: r.left })
      })
    }
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('scroll', reposition, true); window.removeEventListener('resize', reposition) }
  }, [open])

  const toggleOpen = () => {
    if (!open) {
      const r = btnRef.current?.getBoundingClientRect()
      if (r) setMenuPos({ top: r.bottom + 4, left: r.left })
    }
    setOpen(v => !v)
  }

  const run = (action: BulkAction) => {
    setOpen(false)
    onAction(action)
  }
  const entry = (action: BulkAction, Icon: LucideIcon, label: string, title?: string) => (
    <button style={item} {...hover} onClick={() => run(action)} title={title}>
      <Icon size={15} aria-hidden style={{ color: 'var(--text-muted)' }} />
      <span>{label}</span>
    </button>
  )

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.5rem',
      padding: '0.25rem 0.625rem', borderRadius: 'var(--radius)',
      background: 'rgba(76,175,80,0.08)', border: '1px solid rgba(76,175,80,0.3)',
    }}>
      <span style={{ fontSize: '0.82rem', fontWeight: 600 }}>{selectedCount} selected</span>

      <button
        onClick={onClearSelection}
        style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--primary)', padding: '0.2rem 0.4rem' }}
      >
        Clear
      </button>

      {/* Actions dropdown, portalled so the ribbon's overflow does not clip it */}
      <div ref={ref} style={{ position: 'relative', marginLeft: '0.5rem' }}>
        <button
          ref={btnRef}
          className="btn"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8rem', padding: '0.3rem 0.7rem' }}
          onClick={toggleOpen}
          aria-expanded={open}
        >
          Actions <ChevronDown size={14} aria-hidden />
        </button>
        {open && menuPos && createPortal(
          <div ref={menuRef} style={{
            position: 'fixed', top: menuPos.top, left: menuPos.left, zIndex: 400,
            minWidth: 220,
            background: 'var(--bg-color)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: '0.25rem 0',
          }}>
            {entry('label', Tags, 'Label as…', 'Give every selected photo the same species, or mark them empty')}
            {selectedCount === 1 && entry('similar', Search, 'Find similar photos', 'Show the photos that look most like this one')}

            {divider}

            {inatEnabled && (inatConnected
              ? entry('inat', Bird, 'Publish to iNaturalist')
              : (
                <Link to={INAT_CONNECT_PATH} style={item} {...hover} onClick={() => setOpen(false)}
                  title="Connect your iNaturalist account on the Toolkit page, then publish from here">
                  <Bird size={15} aria-hidden style={{ color: 'var(--text-muted)' }} />
                  <span>Connect iNaturalist…</span>
                </Link>
              ))}
            {entry('delete', Trash2, 'Remove photos')}

            {divider}

            {entry('ai', Cpu, 'Run AI again')}
            {canTrain && entry('train', Sparkles, 'Create species ID model…', 'Train an on-device species ID model from the selected labelled photos')}
          </div>,
          document.body,
        )}
      </div>
    </div>
  )
}
