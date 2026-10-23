// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Change the signed-in user's password from Settings (UI audit 05 #1). /reset-password stays for
// the emailed recovery link only. The minimum is the sign-up one, so both forms agree.
import type { SupabaseClient } from '@supabase/supabase-js'
import { MIN_PASSWORD_LENGTH } from './signUp'

/** The first problem with the two fields, or null when the password can be sent. */
export function newPasswordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  if (password !== confirm) return 'The two passwords do not match.'
  return null
}

// Supabase Auth error codes (auth-js ErrorCode) a password change can return.
const MESSAGES: Record<string, string> = {
  same_password: 'That is your current password. Choose a new one.',
  weak_password: 'That password is too weak. Choose a longer one.',
  reauthentication_needed: 'For your security, sign out and sign in again, then change your password.',
  session_not_found: 'Your session has ended. Sign in again, then retry.',
  over_request_rate_limit: 'Too many attempts. Wait a minute, then try again.',
}

export function changePasswordMessage(err: { code?: string; message?: string }): string {
  return (err.code && MESSAGES[err.code]) || err.message || 'Your password was not changed.'
}

/** Resolves when Supabase stored the new password; throws a message the user can read. */
export async function changePassword(db: SupabaseClient, password: string): Promise<void> {
  const { error } = await db.auth.updateUser({ password })
  if (error) throw new Error(changePasswordMessage(error))
}
