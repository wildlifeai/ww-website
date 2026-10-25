// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * UndoToastHost: the one short-lived toast, "Deleted N, Undo" or "Labelled Rat, Undo".
 *
 * There's no general toast system in the app (the notifications feature is DB-backed/persistent),
 * so this is a tiny singleton: mount <UndoToastHost/> once (in App), then call showUndoToast(...)
 * or showErrorToast(...) from anywhere (imported from ./undoToastBus). Auto-dismisses after 6 s
 * (10 s for an error); clicking Undo runs the supplied callback, and a failed undo stays on screen
 * as an error.
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { subscribeUndoToast, type UndoPayload } from './undoToastBus'

export function UndoToastHost() {
  const [toast, setToast] = useState<UndoPayload | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    subscribeUndoToast(setToast)
    return () => subscribeUndoToast(null)
  }, [])

  useEffect(() => {
    if (!toast || busy) return
    const t = setTimeout(() => setToast(null), toast.tone === 'error' ? 10_000 : 6000)
    return () => clearTimeout(t)
  }, [toast, busy])

  if (!toast) return null
  const error = toast.tone === 'error'

  const handleUndo = async () => {
    const cb = toast.onUndo
    if (!cb) return
    setBusy(true)
    try {
      await cb()
      setToast(null)
    } catch (e) {
      // A failed undo (permission, network) stays visible instead of leaving the change silently.
      setToast({ message: e instanceof Error ? e.message : 'Undo failed.', tone: 'error' })
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div
      role={error ? 'alert' : 'status'}
      style={{
        position: 'fixed', bottom: '1.25rem', left: '1.25rem', zIndex: 2000,
        display: 'flex', alignItems: 'center', gap: '0.75rem',
        padding: '0.6rem 0.9rem', borderRadius: 'var(--radius)',
        background: error ? 'var(--error, #ef4444)' : 'var(--text-color)',
        color: error ? '#fff' : 'var(--bg-color)',
        boxShadow: '0 4px 24px rgba(0,0,0,0.25)', fontSize: '0.8125rem', maxWidth: 380,
      }}
    >
      <span>{toast.message}</span>
      {toast.onUndo && (
        <button
          onClick={handleUndo}
          disabled={busy}
          style={{
            background: 'none', border: 'none', cursor: busy ? 'wait' : 'pointer',
            color: 'var(--primary, #3b82f6)', fontWeight: 700, fontSize: '0.8125rem', filter: 'brightness(1.5)',
          }}
        >
          {busy ? 'Undoing…' : 'Undo'}
        </button>
      )}
      <button
        onClick={() => setToast(null)}
        aria-label="Dismiss"
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', display: 'inline-flex', padding: 0 }}
      >
        <X size={14} aria-hidden />
      </button>
    </div>,
    document.body,
  )
}
