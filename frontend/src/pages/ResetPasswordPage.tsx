import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../config/supabase'
import { usePageMeta } from '../lib/pageMeta'

export function ResetPasswordPage() {
  usePageMeta({ title: 'Reset your password' })
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<'waiting' | 'ready' | 'success' | 'error'>('waiting')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    // Supabase JS client automatically picks up tokens from the URL fragment
    // (#access_token=...&refresh_token=...&type=recovery) and fires PASSWORD_RECOVERY
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'PASSWORD_RECOVERY') {
        setStatus('ready')
      }
    })

    // Also check if we already have a session (page might have loaded with tokens already processed)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        // Check if the URL hash contained recovery params
        const hash = window.location.hash
        if (hash.includes('type=recovery') || hash.includes('type=magiclink')) {
          setStatus('ready')
        }
      }
    })

    // Timeout — if no recovery event after 10 seconds, show an error
    const timeout = setTimeout(() => {
      setStatus((prev) => {
        if (prev === 'waiting') {
          setErrorMessage('This password reset link appears to be invalid or expired. Please request a new password reset.')
          return 'error'
        }
        return prev
      })
    }, 10000)

    return () => {
      subscription.unsubscribe()
      clearTimeout(timeout)
    }
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMessage('')

    if (password.length < 6) {
      setErrorMessage('Password must be at least 6 characters long.')
      return
    }

    if (password !== confirmPassword) {
      setErrorMessage('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      const { error } = await supabase.auth.updateUser({ password })
      if (error) {
        setErrorMessage(error.message)
        return
      }
      setStatus('success')
      // Sign out so user can log in fresh with new password
      await supabase.auth.signOut()
    } catch (err) {
      console.error('Unexpected error during password update:', err)
      setErrorMessage('An unexpected error occurred. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: '440px', margin: '4rem auto', padding: '0 1rem' }}>
      <div className="card" style={{ padding: '2.5rem' }}>
        {/* Header Icon */}
        <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
          <div style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            background: status === 'success'
              ? 'linear-gradient(135deg, var(--success), #059669)'
              : 'linear-gradient(135deg, var(--primary), var(--primary-light))',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: '1rem',
            transition: 'background 0.3s ease'
          }}>
            <span style={{ fontSize: '1.5rem', color: 'white' }}>
              {status === 'success' ? '✓' : '🔒'}
            </span>
          </div>
        </div>

        {/* Waiting State */}
        {status === 'waiting' && (
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ margin: '0 0 1rem', fontSize: '1.5rem' }}>Verifying Reset Link…</h2>
            <p style={{ opacity: 0.7, margin: 0, lineHeight: 1.6 }}>
              Please wait while we verify your password reset token.
            </p>
            <div style={{
              marginTop: '2rem',
              display: 'flex',
              justifyContent: 'center'
            }}>
              <div className="spinner" style={{
                width: '32px',
                height: '32px',
                border: '3px solid var(--border)',
                borderTopColor: 'var(--primary)',
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite'
              }} />
            </div>
          </div>
        )}

        {/* Ready — Show Password Form */}
        {status === 'ready' && (
          <>
            <h2 style={{ textAlign: 'center', margin: '0 0 0.5rem', fontSize: '1.5rem' }}>
              Set New Password
            </h2>
            <p style={{ textAlign: 'center', opacity: 0.7, margin: '0 0 2rem', lineHeight: 1.6 }}>
              Enter your new password below. It must be at least 6 characters long.
            </p>

            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label htmlFor="reset-password" style={{
                  display: 'block',
                  marginBottom: '0.375rem',
                  fontSize: '0.875rem',
                  fontWeight: 500
                }}>
                  New Password
                </label>
                <input
                  id="reset-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={6}
                  autoFocus
                  autoComplete="new-password"
                  className="reset-input"
                />
              </div>

              <div>
                <label htmlFor="reset-confirm-password" style={{
                  display: 'block',
                  marginBottom: '0.375rem',
                  fontSize: '0.875rem',
                  fontWeight: 500
                }}>
                  Confirm Password
                </label>
                <input
                  id="reset-confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  minLength={6}
                  autoComplete="new-password"
                  className="reset-input"
                />
              </div>

              {errorMessage && (
                <div style={{
                  padding: '0.75rem 1rem',
                  borderRadius: 'var(--radius)',
                  backgroundColor: 'color-mix(in srgb, var(--error) 10%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--error) 30%, transparent)',
                  color: 'var(--error)',
                  fontSize: '0.875rem',
                  lineHeight: 1.5
                }}>
                  {errorMessage}
                </div>
              )}

              <button
                type="submit"
                className="btn"
                disabled={loading}
                style={{
                  marginTop: '0.5rem',
                  padding: '0.75rem',
                  fontSize: '0.9375rem',
                  opacity: loading ? 0.7 : 1,
                  cursor: loading ? 'not-allowed' : 'pointer'
                }}
              >
                {loading ? 'Updating…' : 'Update Password'}
              </button>
            </form>
          </>
        )}

        {/* Success State */}
        {status === 'success' && (
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ margin: '0 0 0.5rem', fontSize: '1.5rem', color: 'var(--success)' }}>
              Password Updated!
            </h2>
            <p style={{ opacity: 0.7, margin: '0 0 2rem', lineHeight: 1.6 }}>
              Your password has been successfully updated. You can now log in with your new password.
            </p>
            <button
              className="btn"
              onClick={() => navigate('/login')}
              style={{ padding: '0.75rem 2rem', fontSize: '0.9375rem' }}
            >
              Go to Login
            </button>
          </div>
        )}

        {/* Error State */}
        {status === 'error' && (
          <div style={{ textAlign: 'center' }}>
            <h2 style={{ margin: '0 0 0.5rem', fontSize: '1.5rem' }}>Link Expired or Invalid</h2>
            {errorMessage && (
              <p style={{ opacity: 0.7, margin: '0 0 2rem', lineHeight: 1.6 }}>
                {errorMessage}
              </p>
            )}
            <button
              className="btn"
              onClick={() => navigate('/login')}
              style={{ padding: '0.75rem 2rem', fontSize: '0.9375rem' }}
            >
              Back to Login
            </button>
          </div>
        )}
      </div>

      {/* Inline keyframe animation for spinner */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        .reset-input {
          width: 100%;
          padding: 0.625rem 0.75rem;
          border-radius: var(--radius);
          border: 1px solid var(--border);
          font-size: 0.9375rem;
          background-color: var(--bg-color);
          color: var(--text-color);
          outline: none;
          transition: border-color 0.2s;
          box-sizing: border-box;
        }
        .reset-input:focus {
          border-color: var(--primary);
        }
      `}</style>
    </div>
  )
}
