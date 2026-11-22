// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// RealtimeTabOff: the Realtime header tab while none of the user's cameras has sent LoRaWAN data
// (useHasRealtimeData). It stays in its place, greyed out and not a link, so people know the page
// exists; hovering says why, and a click opens a note with the way to connect a camera.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

const WHY = 'None of your cameras in the field is sending live updates yet'

export function RealtimeTabOff({ icon: Icon, text, style }: { icon: LucideIcon; text: string; style: React.CSSProperties }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)

  // Close the note on a click outside it or on Escape.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <span ref={ref} style={{ position: 'relative', display: 'flex', alignItems: 'stretch' }}>
      <button type="button" className="hdr-tab" aria-disabled="true" aria-expanded={open} aria-label={`${text}: ${WHY}`} title={WHY}
        onClick={() => setOpen(o => !o)}
        style={{ ...style, background: 'none', border: 'none', borderBottom: '2px solid transparent', cursor: 'not-allowed', opacity: 0.45, font: 'inherit', fontSize: style.fontSize }}>
        <Icon size={15} aria-hidden="true" />
        <span className="hdr-text">{text}</span>
      </button>
      {open && (
        <div role="note" style={{
          position: 'absolute', top: '100%', left: 0, zIndex: 120, width: 280, marginTop: 4,
          padding: '0.75rem 0.875rem', background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', boxShadow: '0 4px 12px rgba(0,0,0,0.12)', fontSize: '0.8125rem', lineHeight: 1.45,
        }}>
          <p style={{ margin: 0 }}>{WHY}. Realtime opens when a camera in one of your projects sends its first LoRaWAN message.</p>
          <Link to="/guides/lorawan-in-new-zealand" onClick={() => setOpen(false)}
            style={{ display: 'inline-block', marginTop: '0.5rem', color: 'var(--primary)' }}>
            Connect a camera over LoRaWAN
          </Link>
        </div>
      )}
    </span>
  )
}
