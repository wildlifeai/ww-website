/**
 * Uploads straight to S3, when the API says it takes them that way.
 *
 * With the backend on AWS Lambda (`OBJECT_STORE_BACKEND=s3`), a request body is capped near
 * 6 MB, so `apiClient.upload` sends each file to the bucket on a presigned POST first and
 * the endpoint receives `<field>_key` form fields instead of the bytes. `GET
 * /api/uploads/config` decides; every other server, and local dev, keeps multipart bodies.
 * Design: documentation/development reports/2026-10_aws-reannz-migration/README.md
 */

export type UploadPurpose = 'photos' | 'camtrapdp' | 'model'

const PURPOSES: Record<string, UploadPurpose> = {
  '/api/exif/parse': 'photos',
  '/api/camtrapdp/import': 'camtrapdp',
  '/api/models/convert': 'model',
}

/** Files sent to the bucket at once. */
const CONCURRENCY = 4

export interface PresignedPost {
  key: string
  url: string
  fields: Record<string, string>
}

export interface PresignFile {
  name: string
  size: number
  content_type: string
}

export interface UploadDeps {
  getConfig: () => Promise<{ mode?: string } | null>
  presign: (purpose: UploadPurpose, files: PresignFile[]) => Promise<{ files: PresignedPost[] }>
  postFile: (target: PresignedPost, file: File) => Promise<void>
}

/** The upload endpoint a path is, or null for any other request. */
export function uploadPurpose(path: string): UploadPurpose | null {
  return PURPOSES[path.split('?')[0]] ?? null
}

let modePromise: Promise<string> | null = null

/** Forget the server's answer (tests, or after signing in). */
export function resetUploadModeCache(): void {
  modePromise = null
}

function uploadMode(deps: UploadDeps): Promise<string> {
  if (!modePromise) {
    modePromise = deps
      .getConfig()
      .then((config) => config?.mode ?? 'multipart')
      .catch(() => {
        // Not signed in yet, or an older server: multipart, and ask again next time.
        modePromise = null
        return 'multipart'
      })
  }
  return modePromise
}

/** Run `run` over `items`, at most `size` at a time: each worker takes the next item when its last one settles. */
async function inPool<T>(items: T[], size: number, run: (item: T, index: number) => Promise<void>): Promise<void> {
  let next = 0
  const take = (): Promise<void> => {
    if (next >= items.length) return Promise.resolve()
    const index = next++
    return run(items[index], index).then(take)
  }
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, take))
}

/**
 * The form to send to `path`: unchanged, or with each File replaced by the key it was
 * uploaded under (`files` becomes `files_key`, `file` becomes `file_key`), in the same order.
 */
export async function prepareUpload(path: string, formData: FormData, deps: UploadDeps): Promise<FormData> {
  const purpose = uploadPurpose(path)
  if (!purpose) return formData
  const entries = [...formData.entries()]
  const files = entries.map(([, value]) => value).filter((value): value is File => value instanceof File)
  if (!files.length || (await uploadMode(deps)) !== 's3') return formData

  const { files: targets } = await deps.presign(
    purpose,
    files.map((f) => ({ name: f.name, size: f.size, content_type: f.type || 'application/octet-stream' })),
  )
  if (targets.length !== files.length) throw new Error('The server did not presign every file.')
  await inPool(files, CONCURRENCY, (file, i) => deps.postFile(targets[i], file))

  const out = new FormData()
  let i = 0
  for (const [field, value] of entries) {
    if (value instanceof File) out.append(`${field}_key`, targets[i++].key)
    else out.append(field, value)
  }
  return out
}

/** Send one file to S3 on its presigned POST; the file goes last, after the policy fields. */
export async function postFileToS3(target: PresignedPost, file: File): Promise<void> {
  const body = new FormData()
  for (const [name, value] of Object.entries(target.fields)) body.append(name, value)
  body.append('file', file)
  const response = await fetch(target.url, { method: 'POST', body })
  if (!response.ok) throw new Error(`Sending ${file.name} to storage failed (${response.status}).`)
}
