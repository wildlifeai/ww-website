import { describe, expect, it, vi } from 'vitest'
import type { SupabaseClient } from '@supabase/supabase-js'
import { changePassword, changePasswordMessage, newPasswordProblem } from './changePassword'
import { MIN_PASSWORD_LENGTH } from './signUp'

describe('newPasswordProblem', () => {
  it('asks for the sign-up minimum', () => {
    const short = 'a'.repeat(MIN_PASSWORD_LENGTH - 1)
    expect(newPasswordProblem(short, short)).toBe(`Use at least ${MIN_PASSWORD_LENGTH} characters.`)
  })

  it('asks for two matching entries', () => {
    expect(newPasswordProblem('long enough 1', 'long enough 2')).toBe('The two passwords do not match.')
  })

  it('accepts a long, confirmed password', () => {
    const pw = 'a'.repeat(MIN_PASSWORD_LENGTH)
    expect(newPasswordProblem(pw, pw)).toBeNull()
  })
})

describe('changePasswordMessage', () => {
  it('maps the auth error codes to plain sentences', () => {
    expect(changePasswordMessage({ code: 'same_password', message: 'x' })).toMatch(/current password/)
    expect(changePasswordMessage({ code: 'reauthentication_needed' })).toMatch(/sign in again/)
  })

  it('falls back to the server message, then a default', () => {
    expect(changePasswordMessage({ code: 'unexpected_failure', message: 'Boom' })).toBe('Boom')
    expect(changePasswordMessage({})).toBe('Your password was not changed.')
  })
})

describe('changePassword', () => {
  const fakeDb = (error: unknown) => {
    const updateUser = vi.fn(() => Promise.resolve({ data: {}, error }))
    return { db: { auth: { updateUser } } as unknown as SupabaseClient, updateUser }
  }

  it('sends only the password', async () => {
    const { db, updateUser } = fakeDb(null)
    await changePassword(db, 'a new password')
    expect(updateUser).toHaveBeenCalledWith({ password: 'a new password' })
  })

  it('throws the readable message on a refusal', async () => {
    const { db } = fakeDb({ code: 'weak_password', message: 'Password should be stronger' })
    await expect(changePassword(db, 'a new password')).rejects.toThrow(/too weak/)
  })
})
