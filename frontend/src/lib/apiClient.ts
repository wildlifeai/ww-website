import { supabase } from '../config/supabase'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export class ApiError extends Error {
  code: string
  retryable: boolean

  constructor(
    code: string,
    message: string,
    retryable: boolean = false
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.retryable = retryable
  }
}

async function request(path: string, options: RequestInit = {}) {
  const { data: session } = await supabase.auth.getSession()
  const token = session?.session?.access_token

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })

  let body
  try {
    body = await response.json()
  } catch (e) {
    // Some responses (like ZIP downloads) might not be JSON
    return response.blob()
  }

  if (!response.ok) {
    const error = body?.error || { code: 'UNKNOWN', message: 'Request failed' }
    throw new ApiError(error.code, error.message, error.retryable)
  }

  return body
}

export const apiClient = {
  get: (path: string) => request(path),
  post: (path: string, data?: any) => 
    request(path, { method: 'POST', body: JSON.stringify(data) }),
  upload: (path: string, formData: FormData) =>
    request(path, { method: 'POST', body: formData }),
}
