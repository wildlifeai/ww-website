import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Auth } from '@supabase/auth-ui-react'
import { ThemeSupa } from '@supabase/auth-ui-shared'
import { supabase } from '../config/supabase'
import { useAuth } from '../hooks/useAuth'
import { DemoLoginButton } from '../components/common/DemoLoginButton'
import { MIN_PASSWORD_LENGTH, signUpMetadata, signUpProblem, type SignUpFields } from '../lib/signUp'
import { loadGoogleIdentity, newNonce } from '../lib/googleIdentity'

/**
 * Log in (/login) and create an account (/signup), by email or with Google (#116, #187).
 *
 * Google sign-in uses Google's own button when VITE_GOOGLE_CLIENT_ID is set, so the consent
 * screen names this site rather than the Supabase project's domain (lib/googleIdentity.ts);
 * without it, the redirect flow. Redirects and email-confirmation links return to this site's
 * own origin, which is on each Supabase project's redirect allow-list; anything else falls back
 * to the project's Site URL, which on staging is the app's wildlifewatcher:// link that a
 * desktop browser cannot open. supabase-js completes the session from the returning URL. A new account becomes a
 * `users` row in the General organisation (ww-backend `handle_new_user`).
 */
export function LoginPage({ mode = 'sign_in' }: { mode?: 'sign_in' | 'sign_up' }) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [forgotten, setForgotten] = useState(false)

  useEffect(() => {
    if (user) navigate('/')
  }, [user, navigate])

  const siteUrl = window.location.origin
  const signingUp = mode === 'sign_up'
  const title = forgotten ? 'Reset your password' : signingUp ? 'Create your Wildlife Watcher account' : 'Log in to Wildlife Watcher'

  return (
    <div style={{ maxWidth: '400px', margin: '4rem auto', padding: '2rem', backgroundColor: 'var(--surface)', borderRadius: '8px', border: '1px solid var(--border)' }}>
      <h2 style={{ textAlign: 'center', marginBottom: '1.5rem' }}>{title}</h2>

      {forgotten ? (
        <>
          <p style={{ fontSize: '0.8125rem', opacity: 0.75, margin: '0 0 1rem' }}>
            Signed up with Google? You have no password: use Continue with Google, or set one here.
          </p>
          <Auth
            supabaseClient={supabase}
            appearance={AUTH_APPEARANCE}
            theme="light"
            providers={[]}
            redirectTo={siteUrl + '/reset-password'}
            view="forgotten_password"
            showLinks={false}
          />
          <div style={{ textAlign: 'center', marginTop: '1rem' }}>
            <button type="button" onClick={() => setForgotten(false)} style={LINK_BUTTON}>Back to log in</button>
          </div>
        </>
      ) : (
        <>
          <GoogleButton redirectTo={siteUrl + '/'} text={signingUp ? 'signup_with' : 'continue_with'} />
          <Divider />
          {signingUp ? (
            <SignUpForm redirectTo={siteUrl + '/'} />
          ) : (
            <>
              <Auth
                supabaseClient={supabase}
                appearance={AUTH_APPEARANCE}
                theme="light"
                providers={[]}
                redirectTo={siteUrl + '/'}
                view="sign_in"
                showLinks={false}
              />
              <div style={{ textAlign: 'center', marginTop: '0.5rem' }}>
                <button type="button" onClick={() => setForgotten(true)} style={LINK_BUTTON}>Forgot your password?</button>
              </div>
            </>
          )}
          <div style={{ textAlign: 'center', marginTop: '1rem', fontSize: '0.875rem' }}>
            {signingUp
              ? <>Already have an account? <Link to="/login" style={{ color: 'var(--primary)' }}>Log in</Link></>
              : <>No account yet? <Link to="/signup" style={{ color: 'var(--primary)' }}>Create one</Link></>}
          </div>
        </>
      )}

      {!signingUp && !forgotten && (
        <div style={{ textAlign: 'center', marginTop: '1.5rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.625rem' }}>
            Or explore with sample data:
          </div>
          <DemoLoginButton />
        </div>
      )}
    </div>
  )
}

const GOOGLE_CLIENT_ID: string = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''

function GoogleButton({ redirectTo, text }: { redirectTo: string; text: 'signup_with' | 'continue_with' }) {
  return GOOGLE_CLIENT_ID
    ? <GoogleIdentityButton clientId={GOOGLE_CLIENT_ID} text={text} />
    : <GoogleRedirectButton redirectTo={redirectTo} />
}

/** Google's button: the ID token comes straight back here and goes to Supabase. */
function GoogleIdentityButton({ clientId, text }: { clientId: string; text: 'signup_with' | 'continue_with' }) {
  const slot = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([loadGoogleIdentity(), newNonce()])
      .then(([google, nonce]) => {
        if (cancelled || !slot.current) return
        google.initialize({
          client_id: clientId,
          nonce: nonce.hashed,
          ux_mode: 'popup',
          callback: ({ credential }) => {
            supabase.auth
              .signInWithIdToken({ provider: 'google', token: credential, nonce: nonce.raw })
              .then(({ error: err }) => setError(err ? err.message : null))
          },
        })
        // 336 px fills the card: 400 px wide less 2rem padding each side.
        google.renderButton(slot.current, { type: 'standard', theme: 'outline', size: 'large', text, shape: 'rectangular', width: 336, logo_alignment: 'center' })
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [clientId, text])

  return (
    <>
      <div ref={slot} style={{ display: 'flex', justifyContent: 'center', minHeight: 44 }} />
      {error && <p role="alert" style={ERROR_TEXT}>{error}</p>}
    </>
  )
}

/** The redirect flow, for an environment without VITE_GOOGLE_CLIENT_ID. */
function GoogleRedirectButton({ redirectTo }: { redirectTo: string }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const signIn = async () => {
    setBusy(true)
    setError(null)
    try {
      // Leaves the page for Google on success; only a refusal comes back here.
      const { error: err } = await supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo } })
      if (err) setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" onClick={signIn} disabled={busy} style={GOOGLE_BUTTON}>
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
          <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
          <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
          <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
          <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
        </svg>
        Continue with Google
      </button>
      {error && <p role="alert" style={ERROR_TEXT}>{error}</p>}
    </>
  )
}

function SignUpForm({ redirectTo }: { redirectTo: string }) {
  const [fields, setFields] = useState<SignUpFields>({ firstName: '', lastName: '', email: '', password: '' })
  const [error, setError] = useState<string | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const problem = signUpProblem(fields)
    if (problem) { setError(problem); return }
    setBusy(true)
    setError(null)
    try {
      const { data, error: err } = await supabase.auth.signUp({
        email: fields.email.trim(),
        password: fields.password,
        options: { data: signUpMetadata(fields), emailRedirectTo: redirectTo },
      })
      if (err) { setError(err.message); return }
      // With email confirmation on there is no session yet: the user confirms, then lands back
      // here signed in. With it off, the session arrives now and the page redirects home.
      if (!data.session) setSentTo(fields.email.trim())
    } finally {
      setBusy(false)
    }
  }

  const field = (key: keyof SignUpFields) => ({
    value: fields[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setFields(f => ({ ...f, [key]: e.target.value })),
  })

  if (sentTo) {
    return (
      <p role="status" style={{ fontSize: '0.9rem', lineHeight: 1.5 }}>
        Check your inbox at <strong>{sentTo}</strong> for a link to confirm your account. Following
        it signs you in.
      </p>
    )
  }

  return (
    <form onSubmit={submit} noValidate style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.75rem' }}>
        <label style={FIELD}>First name<input {...field('firstName')} autoComplete="given-name" style={INPUT} /></label>
        <label style={FIELD}>Last name<input {...field('lastName')} autoComplete="family-name" style={INPUT} /></label>
      </div>
      <label style={FIELD}>Email<input type="email" {...field('email')} autoComplete="email" style={INPUT} /></label>
      <label style={FIELD}>
        Password
        <input type="password" {...field('password')} autoComplete="new-password" minLength={MIN_PASSWORD_LENGTH} style={INPUT} />
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>At least {MIN_PASSWORD_LENGTH} characters.</span>
      </label>
      {error && <p role="alert" style={ERROR_TEXT}>{error}</p>}
      <button type="submit" className="btn" disabled={busy} style={{ padding: '0.6rem 1rem' }}>
        {busy ? 'Creating your account…' : 'Create account'}
      </button>
    </form>
  )
}

function Divider() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', margin: '1.25rem 0', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
      <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
      or
      <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
    </div>
  )
}

/**
 * ThemeSupa's defaults fail WCAG AA on this page: white on its #3fcf8e brand
 * button is 2:1 and its #7e7e7e labels 3.9:1 on --surface (#212). The brand
 * takes the site's light-mode primary (6.5:1 with white text) and the labels
 * the muted text colour. Hex values, not var(): the Auth UI renders
 * theme="light" whatever the OS scheme, so the dark-mode tokens would not fit.
 */
const AUTH_APPEARANCE = {
  theme: ThemeSupa,
  variables: {
    default: {
      colors: {
        brand: '#006e1c',
        brandAccent: '#005a17',
        inputLabelText: '#5f6368',
      },
    },
  },
}

const LINK_BUTTON: React.CSSProperties = {
  background: 'none', border: 'none', color: 'var(--primary)', cursor: 'pointer',
  fontSize: '0.875rem', textDecoration: 'underline', padding: '0.25rem',
}
const GOOGLE_BUTTON: React.CSSProperties = {
  width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.625rem',
  padding: '0.6rem 1rem', fontSize: '0.9375rem', fontWeight: 500, cursor: 'pointer',
  background: '#fff', color: '#1f1f1f', border: '1px solid #dadce0', borderRadius: 'var(--radius)',
}
const FIELD: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '0.3rem', fontSize: '0.875rem', fontWeight: 500, flex: 1 }
const INPUT: React.CSSProperties = {
  padding: '0.5rem 0.625rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)',
  backgroundColor: 'var(--bg-color)', color: 'var(--text-color)', fontSize: '0.875rem', boxSizing: 'border-box', width: '100%',
}
const ERROR_TEXT: React.CSSProperties = { color: 'var(--error)', fontSize: '0.8125rem', margin: '0.5rem 0 0' }
