import { describe, expect, it } from 'vitest'
import type { User } from '@supabase/supabase-js'
import { mayHaveSession, nextAuthUser } from './authUser'

const user = (id: string, email = `${id}@example.test`) => ({ id, email }) as User

describe('nextAuthUser', () => {
  it('keeps the same object when a token refresh re-sends the same person', () => {
    const current = user('u1')
    expect(nextAuthUser(current, user('u1'), 'TOKEN_REFRESHED')).toBe(current)
    expect(nextAuthUser(current, user('u1'), 'SIGNED_IN')).toBe(current)
    expect(nextAuthUser(current, user('u1'))).toBe(current) // the initial getSession
  })

  it('takes the new value for a different person, a sign-out, or a first sign-in', () => {
    const other = user('u2')
    expect(nextAuthUser(user('u1'), other, 'SIGNED_IN')).toBe(other)
    expect(nextAuthUser(user('u1'), null, 'SIGNED_OUT')).toBeNull()
    const first = user('u1')
    expect(nextAuthUser(null, first, 'SIGNED_IN')).toBe(first)
  })

  it('takes the new value when the user updates their own record', () => {
    const updated = user('u1', 'new@example.test')
    expect(nextAuthUser(user('u1'), updated, 'USER_UPDATED')).toBe(updated)
  })
})

describe('mayHaveSession', () => {
  const storage = (...keys: string[]) => ({ length: keys.length, key: (i: number) => keys[i] ?? null })
  const at = (search = '', hash = '') => ({ search, hash })

  it('is false with no stored session and no sign-in redirect', () => {
    expect(mayHaveSession(storage(), at())).toBe(false)
    expect(mayHaveSession(storage('ww:groupBy', 'sb-abc-auth-token-code-verifier'), at('?tab=x'))).toBe(false)
  })

  it('is true with a stored Supabase session', () => {
    expect(mayHaveSession(storage('ww:groupBy', 'sb-abcdef-auth-token'), at())).toBe(true)
  })

  it('is true on a sign-in redirect, before the session is stored', () => {
    expect(mayHaveSession(storage(), at('', '#access_token=t&type=signup'))).toBe(true)
    expect(mayHaveSession(storage(), at('?code=abc'))).toBe(true)
  })

  it('is true when storage cannot be read', () => {
    const blocked = { get length(): number { throw new Error('SecurityError') }, key: () => null }
    expect(mayHaveSession(blocked, at())).toBe(true)
  })
})
