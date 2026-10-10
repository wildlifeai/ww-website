import { supabase } from '../config/supabase'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export class ApiError extends Error {
  code: string
  retryable: boolean
  /** A FastAPI `detail` that is an object, e.g. the label-map route's `{ message, problems }`. */
  detail?: unknown

  constructor(
    code: string,
    message: string,
    retryable: boolean = false,
    detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = retryable
    this.detail = detail
  }
}

async function request(path: string, options: RequestInit = {}) {
  const { data: session } = await supabase.auth.getSession()
  const token = session?.session?.access_token

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    })
  } catch {
    // fetch() rejects with a TypeError ("Failed to fetch") only for transport-level
    // failures — the request never got an HTTP response: backend unreachable, CORS
    // preflight blocked, connection reset, or the upload was cut off. This is NOT an
    // auth/permission error (those come back as a 401/403 response), so classify it
    // distinctly and let callers show accurate guidance.
    throw new ApiError(
      'NETWORK_ERROR',
      `Could not reach the server at ${API_BASE_URL}. Check that the backend is running and reachable (and, for large uploads, that the connection wasn't dropped).`,
      true,
    )
  }

  // The body stream can only be read ONCE. The old code did response.json() and, on
  // failure, response.blob() — but json() consumes the stream even when it throws, so the
  // blob() call crashed with "body stream already read". Decide up front by content-type
  // and read exactly once.
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    // Genuine binary/download (ZIP, CSV…) on success → hand back the raw blob.
    if (response.ok) return response.blob()
    // Otherwise it's a non-JSON *error*: a gateway/proxy HTML page (502/503/504), or the
    // API base URL is misconfigured so the request landed on the static site / SPA instead
    // of the backend. Surface the real status instead of a cryptic stream error.
    const text = await response.text().catch(() => '')
    throw new ApiError(
      `HTTP_${response.status}`,
      text.trim().slice(0, 300) || `Request failed (${response.status} ${response.statusText}). The API may be unreachable or misconfigured.`,
      response.status >= 500,
    )
  }

  let body
  try {
    body = await response.json()
  } catch {
    // Declared JSON but empty/truncated body.
    if (!response.ok) {
      throw new ApiError(`HTTP_${response.status}`, `Request failed (${response.status} ${response.statusText})`, response.status >= 500)
    }
    return null
  }

  if (!response.ok) {
    // Try ApiResponse envelope format first, then FastAPI HTTPException format
    const error = body?.error || { code: 'UNKNOWN', message: 'Request failed' }
    const detail = body?.detail
    let message = error.message
    if (detail) {
      message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: any) => d.msg || d.message || JSON.stringify(d)).join('; ') : detail.message || JSON.stringify(detail)
    }
    throw new ApiError(error.code || `HTTP_${response.status}`, message, error.retryable, detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : undefined)
  }

  // Honour the {data, error, meta} envelope: handlers that return an error
  // envelope with HTTP 200 (e.g. FEATURE_DISABLED, NOT_IMPLEMENTED, NOT_FOUND)
  // must still surface as a thrown ApiError, not a silent "success".
  if (body && typeof body === 'object' && body.error) {
    throw new ApiError(body.error.code || 'ERROR', body.error.message || 'Request failed', body.error.retryable)
  }

  return body
}

export const apiClient = {
  get: (path: string) => request(path),
  post: (path: string, data?: any) => 
    request(path, { method: 'POST', body: JSON.stringify(data) }),
  patch: (path: string, data?: unknown) =>
    request(path, { method: 'PATCH', body: JSON.stringify(data) }),
  put: (path: string, data?: unknown) =>
    request(path, { method: 'PUT', body: JSON.stringify(data) }),
  upload: (path: string, formData: FormData) =>
    request(path, { method: 'POST', body: formData }),
  del: (path: string, data?: any) =>
    request(path, { method: 'DELETE', body: data ? JSON.stringify(data) : undefined }),
}
