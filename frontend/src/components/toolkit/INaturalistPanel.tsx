import { useState, useEffect, useCallback } from 'react'
import { apiClient, API_BASE_URL } from '../../lib/apiClient'

interface INatStatus {
  connected: boolean
  inat_username?: string
  inat_user_id?: number
  inat_icon_url?: string
}

export function INaturalistPanel() {
  const [status, setStatus] = useState<INatStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const checkStatus = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const res = await apiClient.get('/api/inat/status')
      setStatus(res.data ?? res)
    } catch (e: any) {
      if (e.code === 'UNAUTHORIZED' || e.message?.includes('401')) {
        // User not logged in to WW — iNat panel requires auth
        setStatus(null)
        setError('login_required')
      } else if (e.message?.includes('404')) {
        // iNat feature not enabled
        setStatus(null)
        setError('not_enabled')
      } else {
        setError(e.message || 'Failed to check iNaturalist status')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    checkStatus()

    // Check if we just came back from OAuth redirect
    const params = new URLSearchParams(window.location.search)
    if (params.get('inat') === 'connected') {
      // Clean URL and refresh status
      window.history.replaceState({}, '', window.location.pathname)
      checkStatus()
    }
  }, [checkStatus])

  const handleConnect = async () => {
    try {
      setError(null)
      const res = await apiClient.get('/api/inat/auth')
      const data = res.data ?? res
      if (data.authorization_url) {
        // Redirect to iNaturalist in same window
        window.location.href = data.authorization_url
      }
    } catch (e: any) {
      setError(e.message || 'Failed to start iNaturalist connection')
    }
  }

  const handleDisconnect = async () => {
    try {
      setError(null)
      await apiClient.post('/api/inat/disconnect')
      setStatus({ connected: false })
    } catch (e: any) {
      setError(e.message || 'Failed to disconnect')
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
              onClick={handleConnect}
              disabled={loading}
              style={{
                fontSize: '0.75rem',
                padding: '0.375rem 0.75rem',
                background: 'linear-gradient(135deg, #74ac00, #4a7c00)',
                color: '#fff',
                border: 'none',
              }}
            >
              {loading ? '…' : 'Connect'}
            </button>
          )}
        </div>
      </div>

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
