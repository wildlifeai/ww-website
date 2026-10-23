/* eslint-disable react-refresh/only-export-components */
/**
 * DemoGuard: make the read-only demo obvious in the UI.
 *
 * The demo account is read-only at the DB (project_viewer role) and the API
 * (require_not_demo). This adds the UX layer so demo users never hit a raw
 * error: write controls are visibly disabled with a tooltip, and any blocked
 * action shows a friendly toast instead of a Postgres / 403 message.
 *
 * Usage:
 *   const { isDemo, guard, showDemoToast } = useDemoGuard()
 *   <DemoDisabled tip="Create an account to add projects"><button…/></DemoDisabled>
 *   onClick={guard(() => doWrite())}
 */
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Lock } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'

const DEFAULT_MSG = 'This is disabled in the demo. Exit the demo to work with your own data.'

const DemoToastContext = createContext<(message?: string) => void>(() => {})

export function DemoGuardProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showDemoToast = useCallback((m: string = DEFAULT_MSG) => {
    setMessage(m)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setMessage(null), 4500)
  }, [])

  return (
    <DemoToastContext.Provider value={showDemoToast}>
      {children}
      {message && (
        <div
          role="status"
          style={{
            position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', zIndex: 1000,
            background: 'var(--surface)', color: 'var(--text-color)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius)', padding: '0.75rem 1.25rem', boxShadow: '0 6px 24px rgba(0,0,0,0.2)',
            fontSize: '0.875rem', maxWidth: 440, textAlign: 'center', display: 'flex', gap: '0.5rem', alignItems: 'center',
          }}
        >
          <Lock size={15} aria-hidden="true" style={{ flexShrink: 0 }} /><span>{message}</span>
        </div>
      )}
    </DemoToastContext.Provider>
  )
}

/** Read-only-demo helpers. */
export function useDemoGuard() {
  const { isDemo } = useAuth()
  const showDemoToast = useContext(DemoToastContext)

  // Wrap an action so demo users get a toast instead of executing it.
  const guard = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <T extends (...args: any[]) => any>(action: T, message?: string) =>
      ((...args: Parameters<T>) => {
        if (isDemo) { showDemoToast(message); return undefined }
        return action(...args)
      }) as T,
    [isDemo, showDemoToast],
  )

  return { isDemo, guard, showDemoToast }
}

/**
 * Route guard for write-only pages (e.g. /upload-data). For demo users it shows
 * a friendly "read-only demo" notice instead of the form; real users pass through.
 */
export function RequireNotDemo({ children, feature = 'This feature' }: { children: ReactNode; feature?: string }) {
  const { isDemo } = useDemoGuard()
  if (!isDemo) return <>{children}</>
  return (
    <div className="card" style={{ maxWidth: 560, margin: '3rem auto', padding: '2rem', textAlign: 'center' }}>
      <Lock size={32} color="var(--text-muted)" aria-hidden="true" style={{ marginBottom: '0.5rem' }} />
      <h2 style={{ margin: '0 0 0.5rem' }}>Read-only demo</h2>
      <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
        {feature} is disabled while you're exploring the demo with sample data.
        Create a free account to upload and analyse your own data.
      </p>
      <Link to="/login" className="btn" style={{ textDecoration: 'none' }}>Create an account</Link>
    </div>
  )
}

/**
 * Wrap a write control. For demo users it dims the control, adds a "not-allowed"
 * tooltip, and intercepts the click (showing the toast), without the control
 * needing to know anything about the demo. For real users it renders untouched.
 */
export function DemoDisabled({ children, tip }: { children: ReactNode; tip?: string }) {
  const { isDemo, showDemoToast } = useDemoGuard()
  if (!isDemo) return <>{children}</>
  return (
    <span
      title={tip ?? 'Disabled in the demo'}
      onClickCapture={(e) => { e.preventDefault(); e.stopPropagation(); showDemoToast(tip ? `${tip}. Exit the demo to do this.` : undefined) }}
      style={{ display: 'inline-flex', opacity: 0.5, cursor: 'not-allowed' }}
    >
      {children}
    </span>
  )
}
