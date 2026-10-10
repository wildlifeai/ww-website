import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post, del } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }))

vi.mock('./apiClient', () => {
  class ApiError extends Error {
    code: string
    constructor(code: string, message: string) { super(message); this.code = code }
  }
  return { ApiError, apiClient: { get, post, del } }
})

import { ApiError } from './apiClient'
import { createApiKey, expiryFromDate, isPublicApiDisabled, listApiKeys, revokeApiKey } from './apiKeys'

beforeEach(() => { get.mockReset(); post.mockReset(); del.mockReset() })

describe('api key calls name the organisation', () => {
  it('lists with organisation_id', async () => {
    get.mockResolvedValue({ data: [{ id: 'k1' }] })
    await expect(listApiKeys('org-1')).resolves.toEqual([{ id: 'k1' }])
    expect(get).toHaveBeenCalledWith('/api/v1/api-keys?organisation_id=org-1')
  })

  it('creates with organisation_id, a trimmed name, scopes and expiry', async () => {
    post.mockResolvedValue({ data: { id: 'k1', key: 'ww_live_x' } })
    await createApiKey('org-1', '  sync ', ['devices:read'], null)
    expect(post).toHaveBeenCalledWith('/api/v1/api-keys', {
      organisation_id: 'org-1', name: 'sync', scopes: ['devices:read'], expires_at: null,
    })
  })

  it('revokes with organisation_id', async () => {
    del.mockResolvedValue({ data: { revoked: true } })
    await revokeApiKey('org-1', 'k1')
    expect(del).toHaveBeenCalledWith('/api/v1/api-keys/k1?organisation_id=org-1')
  })
})

describe('isPublicApiDisabled', () => {
  it('is true only for FEATURE_DISABLED', () => {
    expect(isPublicApiDisabled(new ApiError('FEATURE_DISABLED', 'off'))).toBe(true)
    expect(isPublicApiDisabled(new ApiError('UNKNOWN', 'Only the organisation\'s managers'))).toBe(false)
    expect(isPublicApiDisabled(null)).toBe(false)
  })
})

describe('expiryFromDate', () => {
  it('is the end of the picked local day', () => {
    const iso = expiryFromDate('2027-03-04')!
    const d = new Date(iso)
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()]).toEqual([2027, 2, 4, 23, 59])
  })

  it('is null for no date or a bad one', () => {
    expect(expiryFromDate('')).toBeNull()
    expect(expiryFromDate('not a date')).toBeNull()
  })
})
