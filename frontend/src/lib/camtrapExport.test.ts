import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './apiClient'
import { EXPORT_FAILED, SELECT_ONE_PROJECT, camtrapFilename, exportStatus, startCamtrapExport } from './camtrapExport'

vi.mock('../config/supabase', () => ({ supabase: {} }))

const P = '11111111-1111-4111-8111-111111111111'

function fakeSupabase(result: { data?: unknown; error?: unknown }) {
  const invoke = vi.fn().mockResolvedValue(result)
  return { invoke, client: { functions: { invoke } } as never }
}

describe('startCamtrapExport', () => {
  // Tests run in node: a stand-in document whose links record the download.
  let anchor: { href: string; download: string; rel: string; click: ReturnType<typeof vi.fn> }
  beforeEach(() => {
    anchor = { href: '', download: '', rel: '', click: vi.fn() }
    vi.stubGlobal('document', { createElement: () => anchor })
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:zip')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('needs exactly one project', async () => {
    const post = vi.fn()
    await expect(startCamtrapExport([], post, fakeSupabase({}).client)).rejects.toThrow(SELECT_ONE_PROJECT)
    await expect(startCamtrapExport([P, P], post, fakeSupabase({}).client)).rejects.toThrow(SELECT_ONE_PROJECT)
    expect(post).not.toHaveBeenCalled()
  })

  it('starts the export job and returns its id', async () => {
    const post = vi.fn().mockResolvedValue({ data: { job_id: 'job-1' } })
    const sb = fakeSupabase({})
    expect(await startCamtrapExport([P], post, sb.client)).toEqual({ kind: 'job', jobId: 'job-1' })
    expect(post).toHaveBeenCalledWith('/api/exports/camtrapdp', { project_id: P })
    expect(sb.invoke).not.toHaveBeenCalled()
  })

  it('shows the server message when the job cannot start', async () => {
    const post = vi.fn().mockRejectedValue(new ApiError('HTTP_404', 'Not found'))
    await expect(startCamtrapExport([P], post, fakeSupabase({}).client)).rejects.toThrow('Not found')
  })

  it('falls back to the metadata-only package while the job is off', async () => {
    const post = vi.fn().mockRejectedValue(new ApiError('FEATURE_DISABLED', 'off'))
    const sb = fakeSupabase({ data: new Blob(['zip']) })
    expect(await startCamtrapExport([P], post, sb.client)).toEqual({ kind: 'downloaded' })
    expect(sb.invoke).toHaveBeenCalledWith('export-camtrap-dp', { body: { project_id: P } })
    expect(anchor.click).toHaveBeenCalledTimes(1)
    expect(anchor.download).toBe(camtrapFilename(P))
  })

  it('shows the function\'s own error text in the fallback', async () => {
    const post = vi.fn().mockRejectedValue(new ApiError('FEATURE_DISABLED', 'off'))
    const error = { message: 'Edge Function returned a non-2xx status code', context: { json: async () => ({ error: 'Dataset too large: narrow the filters' }) } }
    await expect(startCamtrapExport([P], post, fakeSupabase({ error }).client)).rejects.toThrow('Dataset too large')
  })
})

describe('exportStatus', () => {
  const job = { progress: 0.42, message: 'Added 4 of 10 photos…', error: null }
  it.each([
    [{ ...job, status: 'processing' as const }, 'progress', 'Added 4 of 10 photos… 42%'],
    [{ ...job, status: 'queued' as const, message: null, progress: 0 }, 'progress', 'Preparing the export… 0%'],
    [{ ...job, status: 'completed' as const, message: '10 of 10 photos' }, 'done', '10 of 10 photos'],
    [{ ...job, status: 'completed_with_errors' as const, message: '9 of 10 photos' }, 'warning', '9 of 10 photos'],
    [{ ...job, status: 'failed' as const, error: 'Not a member of this project.' }, 'error', 'Not a member of this project.'],
    [{ ...job, status: 'failed' as const, error: null }, 'error', EXPORT_FAILED],
  ])('%o', (j, tone, text) => {
    expect(exportStatus(j)).toEqual({ tone, text })
  })
})

it('names the file after the project and the day', () => {
  expect(camtrapFilename(P, new Date('2026-10-10T23:00:00Z'))).toBe(`camtrapdp-${P}-2026-10-10.zip`)
})
