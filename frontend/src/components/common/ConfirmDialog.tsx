// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ConfirmDialog: the site's dialog for a destructive step, in place of window.confirm() and
// alert() (UI audit 05 #14). The error, when the step fails, shows inside the dialog.
import { Modal } from '../ui/Modal'

const SECONDARY: React.CSSProperties = {
  padding: '0.4rem 0.9rem', fontSize: '0.82rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'transparent', color: 'var(--text-color)', cursor: 'pointer',
}

export function ConfirmDialog({
  open, title, confirmLabel, busyLabel = 'Working…', busy = false, confirmDisabled = false, error, onConfirm, onCancel, children,
}: {
  open: boolean
  title: string
  confirmLabel: string
  busyLabel?: string
  busy?: boolean
  /** Keeps the confirm button off, e.g. until a typed name matches. */
  confirmDisabled?: boolean
  error?: string | null
  onConfirm: () => void
  onCancel: () => void
  children: React.ReactNode
}) {
  const off = busy || confirmDisabled
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      footer={
        <>
          <button type="button" onClick={onCancel} style={SECONDARY}>Cancel</button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={off}
            style={{
              padding: '0.4rem 0.9rem', fontSize: '0.82rem', fontWeight: 600, border: 'none', borderRadius: 'var(--radius)',
              background: 'var(--error, #f44336)', color: '#fff', cursor: off ? 'not-allowed' : 'pointer', opacity: off ? 0.5 : 1,
            }}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', fontSize: '0.85rem' }}>
        {children}
        {error && <p role="alert" style={{ margin: 0, color: 'var(--error, #f44336)', fontSize: '0.8125rem' }}>{error}</p>}
      </div>
    </Modal>
  )
}
