import { describe, expect, it } from 'vitest'
import { newNonce } from './googleIdentity'

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}

describe('newNonce', () => {
  it('gives Google the hex SHA-256 of the raw value Supabase checks it against', async () => {
    const { raw, hashed } = await newNonce()
    expect(hashed).toBe(await sha256Hex(raw))
    expect(hashed).toMatch(/^[0-9a-f]{64}$/)
  })

  it('hashes the way Supabase expects: a known vector', async () => {
    // printf 'abc' | sha256sum
    expect(await sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })

  it('is fresh every time', async () => {
    const [a, b] = await Promise.all([newNonce(), newNonce()])
    expect(a.raw).not.toBe(b.raw)
  })
})
