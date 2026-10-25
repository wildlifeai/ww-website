import { describe, expect, it } from 'vitest'
import { deriveAnnotationStatus } from './annotationStatus'

describe('deriveAnnotationStatus', () => {
  it('ranks error, then reviewed, then ai, then pending', () => {
    expect(deriveAnnotationStatus({ hasReviewed: true, hasAi: true, hasError: true })).toBe('issue')
    expect(deriveAnnotationStatus({ hasReviewed: true, hasAi: true })).toBe('reviewed')
    expect(deriveAnnotationStatus({ hasReviewed: false, hasAi: true })).toBe('ai')
    expect(deriveAnnotationStatus({ hasReviewed: false, hasAi: false })).toBe('pending')
  })
})
