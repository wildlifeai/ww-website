import { describe, expect, it } from 'vitest'
import { stripFrontmatter } from './guideText'

describe('stripFrontmatter', () => {
  it('drops the block at the top and keeps the text', () => {
    expect(stripFrontmatter('---\ntitle: A\norder: 2\n---\n# A\n\nText.\n')).toBe('# A\n\nText.\n')
  })

  it('reads CRLF files', () => {
    expect(stripFrontmatter('---\r\ntitle: A\r\n---\r\nText')).toBe('Text')
  })

  it('leaves a file without frontmatter alone, and a later --- rule too', () => {
    expect(stripFrontmatter('# A\n\n---\n\nB')).toBe('# A\n\n---\n\nB')
  })
})
