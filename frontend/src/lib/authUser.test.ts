import { describe, expect, it } from 'vitest'
import type { User } from '@supabase/supabase-js'
import { nextAuthUser } from './authUser'

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
