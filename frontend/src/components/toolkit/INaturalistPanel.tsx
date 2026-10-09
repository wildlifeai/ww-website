import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { useAuth } from '../../hooks/useAuth'

interface INatStatus {
  connected: boolean
  inat_username?: string
  inat_user_id?: number
  inat_icon_url?: string
}

const INAT_TOKEN_URL = 'https://www.inaturalist.org/users/api_token'

/** The panel's error for a failed status check. The first two hide the panel. */
function statusError(e: { code?: string; message?: string }): string {
  // User not logged in to WW, and the iNat panel requires auth
  if (e.code === 'UNAUTHORIZED' || e.message?.includes('401')) return 'login_required'
  // iNat feature not enabled
  if (e.message?.includes('404')) return 'not_enabled'
  return e.message || 'Failed to check iNaturalist status'
}

export function INaturalistPanel() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  // The connection is per user, and the panel hides itself when signed out (a 401).
  const statusKey = ['inat', 'status', user?.id]
  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: async (): Promise<INatStatus> => {
      const res = await apiClient.get('/api/inat/status')
      return res.data ?? res
    },
    // A 401 or 404 is an answer, not a blip: hide the panel without a retry first.
    retry: false,
  })
  const status = statusQuery.data ?? null
  const loading = statusQuery.isFetching
  // Errors from the connect and disconnect actions; a failed status check is read from the query.
  const [actionError, setActionError] = useState<string | null>(null)
  const error = actionError ?? (statusQuery.isError ? statusError(statusQuery.error) : null)
  // Pathway 2 (pasted personal token) state
  const [showPaste, setShowPaste] = useState(false)
  const [tokenInput, setTokenInput] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Back from the OAuth redirect: clean the URL. The status read on mount sees the new link.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('inat') === 'connected') {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  // Pathway 2: submit the personal API token the user pasted. No OAuth app /
  // callback URL needed — the token is validated server-side, then stored.
  const handleSubmitToken = async () => {
    const token = tokenInput.trim()
    if (!token) { setActionError('Paste your iNaturalist API token first'); return }
    try {
      setSubmitting(true)
      setActionError(null)
      const res = await apiClient.post('/api/inat/token', { api_token: token })
      const data = res.data ?? res
      queryClient.setQueryData<INatStatus>(statusKey, { connected: !!data.connected, inat_username: data.inat_username })
      setTokenInput('')
      setShowPaste(false)
    } catch (e: any) {
      setActionError(e.message || 'That token was rejected — make sure you copied the full api_token')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      setActionError(null)
      await apiClient.post('/api/inat/disconnect')
      queryClient.setQueryData<INatStatus>(statusKey, { connected: false })
    } catch (e: any) {
      setActionError(e.message || 'Failed to disconnect')
    }
  }

  // Don't render if feature is not enabled
  if (error === 'not_enabled') return null

  // Don't render if user is not logged in
  if (error === 'login_required') return null

  return (
    <div
      className="card"
      style={{
        marginBottom: '1.5rem',
        padding: '1rem 1.25rem',
        borderLeft: status?.connected
          ? '3px solid var(--success)'
          : '3px solid var(--border)',
        transition: 'border-color 0.3s',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '1rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {/* iNat logo/icon */}
          <div
            style={{
              width: '2rem',
              height: '2rem',
              borderRadius: '50%',
              background: status?.connected
                ? 'linear-gradient(135deg, #74ac00, #4a7c00)'
                : 'var(--surface-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '1rem',
              flexShrink: 0,
            }}
          >
            {status?.inat_icon_url ? (
              <img
                src={status.inat_icon_url}
                alt="iNat avatar"
                style={{
                  width: '100%',
                  height: '100%',
                  borderRadius: '50%',
                  objectFit: 'cover',
                }}
              />
            ) : (
              '🌿'
            )}
          </div>

          <div>
            <div style={{ fontWeight: 600, fontSize: '0.875rem' }}>
              iNaturalist
            </div>
            {loading ? (
              <div style={{ fontSize: '0.75rem', opacity: 0.5 }}>
                Checking connection…
              </div>
            ) : status?.connected ? (
              <div style={{ fontSize: '0.75rem', color: 'var(--success)' }}>
                ✓ Connected as{' '}
                <strong>{status.inat_username || 'user'}</strong>
              </div>
            ) : (
              <div style={{ fontSize: '0.75rem', opacity: 0.5 }}>
                Not connected
              </div>
            )}
          </div>
        </div>

        <div>
          {status?.connected ? (
            <button
              className="btn"
              onClick={handleDisconnect}
              style={{
                fontSize: '0.75rem',
                padding: '0.375rem 0.75rem',
                background: 'var(--surface-2)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
              }}
            >
              Disconnect
            </button>
          ) : (
            <button
              className="btn"
              onClick={() => setShowPaste((v) => !v)}
              disabled={loading}
              style={{
                fontSize: '0.75rem',
                padding: '0.375rem 0.75rem',
                background: 'linear-gradient(135deg, #74ac00, #4a7c00)',
                color: '#fff',
                border: 'none',
              }}
            >
              {loading ? '…' : showPaste ? 'Cancel' : 'Connect'}
            </button>
          )}
        </div>
      </div>

      {/* Pathway 2: paste a personal API token (no OAuth app required). */}
      {!status?.connected && showPaste && (
        <div style={{ marginTop: '0.85rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontSize: '0.75rem', opacity: 0.8 }}>
            1. Open{' '}
            <a href={INAT_TOKEN_URL} target="_blank" rel="noopener noreferrer" style={{ color: '#74ac00', fontWeight: 600 }}>
              your iNaturalist API token ↗
            </a>{' '}
            (log in to iNaturalist if asked) and copy the long <code>api_token</code> value.
            <br />2. Paste it below and Connect. The token lasts ~24h, so you may need to re-paste a fresh one daily.
          </div>
          <textarea
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Paste your iNaturalist api_token here…"
            rows={3}
            spellCheck={false}
            style={{
              width: '100%', fontFamily: 'monospace', fontSize: '0.7rem',
              padding: '0.5rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
              background: 'var(--surface-2)', color: 'var(--text)', resize: 'vertical',
            }}
          />
          <div>
            <button
              className="btn"
              onClick={handleSubmitToken}
              disabled={submitting || !tokenInput.trim()}
              style={{
                fontSize: '0.75rem', padding: '0.375rem 0.9rem',
                background: submitting || !tokenInput.trim() ? 'var(--surface-2)' : 'linear-gradient(135deg, #74ac00, #4a7c00)',
                color: submitting || !tokenInput.trim() ? 'var(--text)' : '#fff',
                border: 'none', cursor: submitting || !tokenInput.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {submitting ? 'Connecting…' : 'Connect'}
            </button>
          </div>
        </div>
      )}

      {error && error !== 'login_required' && error !== 'not_enabled' && (
        <div
          style={{
            marginTop: '0.5rem',
            fontSize: '0.75rem',
            color: 'var(--error)',
          }}
        >
          {error}
        </div>
      )}
    </div>
  )
}
