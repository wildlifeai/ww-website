// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The one confirmation for settings that save on change (UI audit 05 #14): "Saved" fades after a
// moment, an error stays until the next save, so a refusal is never missed.
import { useCallback, useEffect, useRef, useState } from 'react'

export interface SaveState { ok: boolean; text: string }

type Timer = { current: ReturnType<typeof setTimeout> | null }

function stop(timer: Timer) {
  if (timer.current) clearTimeout(timer.current)
  timer.current = null
}

export function useSaveStatus(fadeMs = 2500) {
  const [status, setStatus] = useState<SaveState | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** Report a save: no text means "Saved", an error passes its message. */
  const report = useCallback((ok: boolean, text?: string) => {
    stop(timer)
    setStatus({ ok, text: text ?? (ok ? 'Saved' : 'Not saved.') })
    if (ok) timer.current = setTimeout(() => setStatus(null), fadeMs)
  }, [fadeMs])

  const reset = useCallback(() => { stop(timer); setStatus(null) }, [])

  useEffect(() => () => stop(timer), [])

  return { status, report, reset }
}
