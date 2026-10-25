// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ChangePasswordForm: set a new password for the signed-in account (UI audit 05 #1), through
// lib/changePassword. The recovery page stays for the emailed link only.
import { useState } from 'react'
import { supabase } from '../../config/supabase'
import { changePassword, newPasswordProblem } from '../../lib/changePassword'
import { SaveStatus } from './SaveStatus'
import type { SaveState } from '../../hooks/useSaveStatus'

const INPUT: React.CSSProperties = {
  padding: '0.4rem 0.55rem', fontSize: '0.8125rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
  background: 'var(--surface)', color: 'var(--text-color)', width: 220, boxSizing: 'border-box',
}
const LABEL: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.8125rem' }

export function ChangePasswordForm() {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<SaveState | null>(null)

  const problem = newPasswordProblem(password, confirm)
  // Say what is wrong once both fields hold something, not while the first is being typed.
  const shownProblem = password && confirm ? problem : null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (problem) return
    setSaving(true)
    setMsg(null)
    try {
      await changePassword(supabase, password)
      setPassword('')
      setConfirm('')
      setMsg({ ok: true, text: 'Password changed' })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Your password was not changed.' })
    } finally {
      setSaving(false)
    }
  }

  const edit = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    set(e.target.value)
    setMsg(null)
  }

  return (
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={LABEL}>
          <span style={{ color: 'var(--text-muted)' }}>New password</span>
          <input type="password" autoComplete="new-password" value={password} onChange={edit(setPassword)} style={INPUT} />
        </label>
        <label style={LABEL}>
          <span style={{ color: 'var(--text-muted)' }}>Confirm new password</span>
          <input type="password" autoComplete="new-password" value={confirm} onChange={edit(setConfirm)} style={INPUT} />
        </label>
        <button type="submit" className="btn" disabled={!!problem || saving} style={{ fontSize: '0.8125rem', padding: '0.45rem 0.9rem', opacity: problem || saving ? 0.6 : 1 }}>
          {saving ? 'Saving…' : 'Change password'}
        </button>
      </div>
      {shownProblem && <span style={{ fontSize: '0.75rem', color: 'var(--error, #ef4444)' }}>{shownProblem}</span>}
      <SaveStatus status={msg} />
    </form>
  )
}
