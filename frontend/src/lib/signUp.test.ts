import { describe, expect, it } from 'vitest'
import { signUpMetadata, signUpProblem } from './signUp'

const ok = { firstName: 'Mary Ann', lastName: 'Te Rangi', email: 'mary@example.test', password: 'kiwi-kākā-1' }

describe('signUp', () => {
  it('accepts a complete form', () => {
    expect(signUpProblem(ok)).toBeNull()
  })

  it('names the first problem', () => {
    expect(signUpProblem({ ...ok, firstName: '  ' })).toBe('Enter your first name.')
    expect(signUpProblem({ ...ok, lastName: '' })).toBe('Enter your last name.')
    expect(signUpProblem({ ...ok, email: 'mary@example' })).toBe('Enter a valid email address.')
    expect(signUpProblem({ ...ok, password: 'short' })).toBe('Use a password of at least 8 characters.')
  })

  it('sends given and family names, so a two-word first name stays whole', () => {
    expect(signUpMetadata({ firstName: ' Mary Ann ', lastName: 'Te Rangi' })).toEqual({
      given_name: 'Mary Ann',
      family_name: 'Te Rangi',
      name: 'Mary Ann Te Rangi',
    })
  })
})
