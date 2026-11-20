import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  postFileToS3,
  prepareUpload,
  resetUploadModeCache,
  uploadPurpose,
  type PresignFile,
  type UploadDeps,
  type UploadPurpose,
} from './objectStoreUpload'

function deps(mode: string | Error = 's3'): UploadDeps & { posted: string[] } {
  const posted: string[] = []
  return {
    posted,
    getConfig: vi.fn(async () => {
      if (mode instanceof Error) throw mode
      return { mode }
    }),
    presign: vi.fn(async (_purpose: UploadPurpose, files: PresignFile[]) => ({
      files: files.map((f, i) => ({ key: `incoming/u1/up/${i}-${f.name}`, url: 'https://bucket', fields: { key: `k${i}` } })),
    })),
    postFile: vi.fn(async (_target, file: File) => {
      posted.push(file.name)
    }),
  }
}

function form(): FormData {
  const fd = new FormData()
  fd.append('files', new File(['a'], 'A.JPG', { type: 'image/jpeg' }))
  fd.append('files', new File(['bb'], 'B.JPG', { type: 'image/jpeg' }))
  fd.append('paths', 'card/A.JPG')
  fd.append('run_ai', 'true')
  return fd
}

beforeEach(() => resetUploadModeCache())

describe('uploads straight to S3', () => {
  it('knows the three upload endpoints and nothing else', () => {
    expect(uploadPurpose('/api/exif/parse')).toBe('photos')
    expect(uploadPurpose('/api/camtrapdp/import')).toBe('camtrapdp')
    expect(uploadPurpose('/api/models/convert?x=1')).toBe('model')
    expect(uploadPurpose('/api/brain/review')).toBeNull()
  })

  it('replaces each file with its key, in order, and keeps the other fields', async () => {
    const d = deps('s3')
    const out = await prepareUpload('/api/exif/parse', form(), d)
    expect(out.getAll('files')).toEqual([])
    expect(out.getAll('files_key')).toEqual(['incoming/u1/up/0-A.JPG', 'incoming/u1/up/1-B.JPG'])
    expect(out.getAll('paths')).toEqual(['card/A.JPG'])
    expect(out.get('run_ai')).toBe('true')
    expect(d.presign).toHaveBeenCalledWith('photos', [
      { name: 'A.JPG', size: 1, content_type: 'image/jpeg' },
      { name: 'B.JPG', size: 2, content_type: 'image/jpeg' },
    ])
    expect(d.posted.sort()).toEqual(['A.JPG', 'B.JPG'])
  })

  it('sends every file, never more than four at once', async () => {
    const d = deps('s3')
    let inFlight = 0
    let most = 0
    d.postFile = vi.fn(async (_target, file: File) => {
      most = Math.max(most, ++inFlight)
      await new Promise((resolve) => setTimeout(resolve, 1))
      inFlight--
      d.posted.push(file.name)
    })
    const fd = new FormData()
    for (let i = 0; i < 10; i++) fd.append('files', new File(['x'], `P${i}.JPG`, { type: 'image/jpeg' }))
    const out = await prepareUpload('/api/exif/parse', fd, d)
    expect(most).toBe(4)
    expect(d.posted).toHaveLength(10)
    expect(out.getAll('files_key')).toEqual(Array.from({ length: 10 }, (_, i) => `incoming/u1/up/${i}-P${i}.JPG`))
  })

  it('sends the form unchanged when the server takes multipart', async () => {
    const d = deps('multipart')
    const fd = form()
    expect(await prepareUpload('/api/exif/parse', fd, d)).toBe(fd)
    expect(d.presign).not.toHaveBeenCalled()
  })

  it('falls back to multipart when the config call fails, and asks again next time', async () => {
    const d = deps(new Error('401'))
    const fd = form()
    expect(await prepareUpload('/api/exif/parse', fd, d)).toBe(fd)
    await prepareUpload('/api/exif/parse', form(), d)
    expect(d.getConfig).toHaveBeenCalledTimes(2)
  })

  it('asks the server once while it answers', async () => {
    const d = deps('s3')
    await prepareUpload('/api/exif/parse', form(), d)
    await prepareUpload('/api/models/convert', form(), d)
    expect(d.getConfig).toHaveBeenCalledTimes(1)
  })

  it('leaves other requests alone', async () => {
    const d = deps('s3')
    const fd = form()
    expect(await prepareUpload('/api/clustering/analyze', fd, d)).toBe(fd)
    expect(d.getConfig).not.toHaveBeenCalled()
  })

  it('posts the policy fields before the file and fails loudly on a refusal', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = init.body as FormData
      expect([...body.keys()]).toEqual(['key', 'policy', 'file'])
      return new Response(null, { status: 403 })
    })
    vi.stubGlobal('fetch', fetchMock)
    try {
      await expect(
        postFileToS3({ key: 'k', url: 'https://bucket', fields: { key: 'k', policy: 'p' } }, new File(['x'], 'X.JPG')),
      ).rejects.toThrow('Sending X.JPG to storage failed (403).')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
