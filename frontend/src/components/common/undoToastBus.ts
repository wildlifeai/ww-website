// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
/** Tiny event bus for the undo toast, kept out of the component file so both the emitter
 *  (showUndoToast) and the host can share module state without tripping react-refresh. */

export interface UndoPayload {
  message: string
  /** Offers Undo when given. A label write always passes one (UI audit 03 #10). */
  onUndo?: () => void | Promise<void>
  /** 'error' for a write the database refused: red, no Undo, stays longer. */
  tone?: 'info' | 'error'
}

let emit: ((p: UndoPayload | null) => void) | null = null

/** Show an undo toast from anywhere in the app. No-op if the host isn't mounted. */
export function showUndoToast(payload: UndoPayload): void {
  emit?.(payload)
}

/** Show why a write did not happen, in the same place as the undo toast. */
export function showErrorToast(message: string): void {
  emit?.({ message, tone: 'error' })
}

/** Internal: the host registers its setter here. */
export function subscribeUndoToast(fn: ((p: UndoPayload | null) => void) | null): void {
  emit = fn
}
