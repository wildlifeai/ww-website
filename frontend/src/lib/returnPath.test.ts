import { describe, expect, it } from 'vitest'
import { returnPath } from './returnPath'

describe('returnPath', () => {
  it('returns the location RequireAuth passed, with its query and hash', () => {
    expect(returnPath({ from: { pathname: '/annotations', search: '?deployment=d1', hash: '#top' } }))
      .toBe('/annotations?deployment=d1#top')
    expect(returnPath({ from: { pathname: '/insights' } })).toBe('/insights')
  })

  it('accepts a path string', () => {
    expect(returnPath({ from: '/settings' })).toBe('/settings')
  })

  it('goes home without a usable state', () => {
    expect(returnPath(null)).toBe('/')
    expect(returnPath(undefined)).toBe('/')
    expect(returnPath({})).toBe('/')
    expect(returnPath({ from: 42 })).toBe('/')
    expect(returnPath({ from: { search: '?x=1' } })).toBe('/')
  })

  it('never leaves the site', () => {
    expect(returnPath({ from: 'https://example.test/' })).toBe('/')
    expect(returnPath({ from: '//example.test/x' })).toBe('/')
    expect(returnPath({ from: '/\\example.test' })).toBe('/')
  })

  it('never returns to a sign-in page', () => {
    expect(returnPath({ from: '/login' })).toBe('/')
    expect(returnPath({ from: { pathname: '/signup', search: '?a=1' } })).toBe('/')
    expect(returnPath({ from: '/reset-password' })).toBe('/')
    expect(returnPath({ from: '/login-help' })).toBe('/login-help')
  })
})
