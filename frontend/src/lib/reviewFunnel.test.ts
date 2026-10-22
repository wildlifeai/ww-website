import { describe, expect, it } from 'vitest'
import { funnelLabel } from './reviewFunnel'

describe('funnelLabel', () => {
  it('names the known review states in plain words', () => {
    expect(funnelLabel('unreviewed')).toBe('Not reviewed')
    expect(funnelLabel('ai_reviewed')).toBe('Labelled by AI')
    expect(funnelLabel('human_reviewed')).toBe('Checked by a person')
    expect(funnelLabel('expert_reviewed')).toBe('Checked by an expert')
    expect(funnelLabel('consensus_approved')).toBe('Approved by consensus')
  })

  it('reads an unknown key as its words', () => {
    expect(funnelLabel('needs_second_look')).toBe('Needs second look')
    expect(funnelLabel('')).toBe('')
  })
})
