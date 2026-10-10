import React, { useRef, useEffect, useState, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Link, NavLink, Navigate, useLocation, useParams } from 'react-router-dom'
import { ChartLine, RadioTower, Tags, Upload, Wrench, type LucideIcon } from 'lucide-react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { useAuth } from './hooks/useAuth'
import { ProjectSelectionProvider } from './hooks/useProjectSelection'
import { GlobalProjectSelector } from './components/common/GlobalProjectSelector'
import { useHasActiveDeployments } from './hooks/useHasActiveDeployments'
import { useNotifications, type AppNotification } from './hooks/useNotifications'
import { InatAutoSync } from './components/settings/InatAutoSync'
import { useIsAdmin } from './hooks/useIsAdmin'
import { AdminUsagePage } from './pages/AdminUsagePage'
import { AdminDevicesPage } from './pages/AdminDevicesPage'
import { UploadQuotaBanner } from './components/UploadQuotaBanner'
import { InvitationsBanner } from './components/InvitationsBanner'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { MyDataPage } from './pages/MyDataPage'
import { ManifestPage } from './pages/ManifestPage'
import { UploadModelPage } from './pages/UploadModelPage'
import { UploadDataPage } from './pages/UploadDataPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { PrivacyPolicyPage } from './pages/PrivacyPolicyPage'
import { TermsOfServicePage } from './pages/TermsOfServicePage'
import { ResourcesPage } from './pages/ResourcesPage'
import { FaqPage } from './pages/FaqPage'

// Guides are lazy-loaded so the markdown renderer stays out of the main bundle.
const GuidesPage = React.lazy(() => import('./pages/GuidesPage'))
const GuideDetailPage = React.lazy(() => import('./pages/GuideDetailPage'))

import { ReportingPage } from './pages/ReportingPage'
import { ClusterReviewPage } from './pages/ClusterReviewPage'
import { UmapExplorerPage } from './pages/UmapExplorerPage'
import { ReviewQueuePage } from './pages/ReviewQueuePage'
import { DatasetHealthPage } from './pages/DatasetHealthPage'
import { AnnotationsPage } from './pages/AnnotationsPage'
import { InsightsPage } from './pages/InsightsPage'
import { ToolkitPage } from './pages/ToolkitPage'
import { FieldPage } from './pages/FieldPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { ProcessingHistoryPage } from './pages/ProcessingHistoryPage'
import { SettingsPage } from './pages/SettingsPage'
import { UploadLogsPage } from './pages/UploadLogsPage'
import { FourZeroFourPage } from './pages/FourZeroFourPage'
import { UploadProvider } from './contexts/UploadContext'
import { DemoGuardProvider, RequireNotDemo } from './components/common/DemoGuard'
import { ProgressDock } from './components/upload/ProgressDock'
import { UndoToastHost } from './components/common/UndoToast'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from './lib/apiClient'
import './styles/index.css'

// ─────────────────────────────────────────────────────────────────────────────
// Auth guard
// ─────────────────────────────────────────────────────────────────────────────

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Legacy-path redirect that preserves the query string (e.g. /results?tab=projects
// → /insights?tab=projects), so old bookmarks keep their sub-tab.
function RedirectTo({ to }: { to: string }) {
  const { search } = useLocation()
  return <Navigate to={`${to}${search}`} replace />
}

// The old /analysis/:id page showed mock data; the real per-deployment results
// live on /reporting/:id. Redirect (keeps existing buttons and bookmarks working).
function RedirectToReporting() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  return <Navigate to={`/reporting/${deployment_id}`} replace />
}

// The old /explore image grid is now redundant — the Annotations page does the
// grid, clustering, bulk cluster-confirm, and similar-image search. Redirect.
function RedirectExploreToAnnotations() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  return <Navigate to={`/annotations?deployment=${deployment_id}`} replace />
}

// ─────────────────────────────────────────────────────────────────────────────
// AccountMenu — user email dropdown
// ─────────────────────────────────────────────────────────────────────────────

function AccountMenu({ email, isOrgManager, isAdmin, onLogout, unreadCount, recent }: {
  email: string
  isOrgManager: boolean
  isAdmin: boolean
  onLogout: () => void
  unreadCount: number
  recent: AppNotification[]
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const itemStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: '0.5rem',
    padding: '0.45rem 0.875rem', fontSize: '0.8125rem',
    border: 'none', backgroundColor: 'transparent',
    color: 'var(--text-color)', cursor: 'pointer', width: '100%', textAlign: 'left',
    textDecoration: 'none',
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(v => !v)}
        title={email}
        aria-label={`Account: ${email}`}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 34, padding: 0,
          border: '1px solid var(--border)', borderRadius: '50%',
          backgroundColor: 'var(--primary)', color: '#fff',
          fontSize: '0.85rem', fontWeight: 700, textTransform: 'uppercase',
          cursor: 'pointer', lineHeight: 1,
        }}
      >
        {(email.trim()[0] || '?')}
      </button>

      {/* Unread badge on the avatar */}
      {unreadCount > 0 && (
        <span style={{
          position: 'absolute', top: -6, right: -6, zIndex: 1,
          minWidth: 17, height: 17, padding: '0 4px', borderRadius: 9,
          background: '#ef4444', color: '#fff', fontSize: '0.65rem', fontWeight: 700,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 0 0 2px var(--surface)', pointerEvents: 'none',
        }}>
          {unreadCount > 9 ? '9+' : unreadCount}
        </span>
      )}

      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 200,
          backgroundColor: 'var(--bg-color)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
          minWidth: '240px', padding: '0.25rem 0',
        }}>
          {/* Signed-in identity */}
          <div style={{ padding: '0.4rem 0.875rem 0.5rem', borderBottom: '1px solid var(--border)', marginBottom: '0.25rem' }}>
            <div style={{ fontSize: '0.7rem', opacity: 0.5 }}>Signed in as</div>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{email}</div>
          </div>
          {/* Recent notifications preview */}
          {recent.length > 0 && (
            <>
              <div style={{ padding: '0.35rem 0.875rem 0.2rem', fontSize: '0.7rem', fontWeight: 700, opacity: 0.5, textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Recent
              </div>
              {recent.slice(0, 5).map(n => (
                <Link
                  key={n.id}
                  to="/notifications"
                  onClick={() => setOpen(false)}
                  style={{ ...itemStyle, alignItems: 'flex-start', flexDirection: 'column', gap: '0.1rem', opacity: n.read_at ? 0.6 : 1 }}
                  onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
                  onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
                >
                  <span style={{ fontWeight: n.read_at ? 500 : 700, fontSize: '0.78rem', whiteSpace: 'normal' }}>
                    {!n.read_at && <span style={{ color: '#ef4444', marginRight: 4 }}>•</span>}{n.title}
                  </span>
                </Link>
              ))}
              <div style={{ height: '1px', backgroundColor: 'var(--border)', margin: '0.25rem 0' }} />
            </>
          )}
          <Link
            to="/notifications"
            onClick={() => setOpen(false)}
            style={itemStyle}
            onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
            onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
          >
            🔔 Notifications{unreadCount > 0 ? ` (${unreadCount})` : ''}{recent.length > 0 ? ' — View all' : ''}
          </Link>
          <Link
            to="/processing"
            onClick={() => setOpen(false)}
            style={itemStyle}
            onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
            onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
          >
            🗂 Processing history
          </Link>
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            style={itemStyle}
            onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
            onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
          >
            ⚙ Settings
          </Link>
          {isAdmin && (
            <Link
              to="/admin/usage"
              onClick={() => setOpen(false)}
              style={itemStyle}
              onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
              onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
            >
              📊 User usage
            </Link>
          )}
          {isAdmin && (
            <Link
              to="/admin/devices"
              onClick={() => setOpen(false)}
              style={itemStyle}
              onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
              onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
            >
              📡 Devices
            </Link>
          )}
          {isOrgManager && (
            <Link
              to="/upload-model"
              onClick={() => setOpen(false)}
              style={itemStyle}
              onMouseEnter={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'rgba(76,175,80,0.07)')}
              onMouseLeave={e => ((e.currentTarget as HTMLElement).style.backgroundColor = 'transparent')}
            >
              🤖 Upload Model
            </Link>
          )}
          <div style={{ height: '1px', backgroundColor: 'var(--border)', margin: '0.25rem 0' }} />
          <button
            onClick={() => { setOpen(false); onLogout() }}
            style={itemStyle}
            onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(76,175,80,0.07)')}
            onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            ← Logout
          </button>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadNavButton: the header Upload button, a link to the upload page
// ─────────────────────────────────────────────────────────────────────────────

function UploadNavButton() {
  return (
    <Link
      to="/upload-data"
      className="btn"
      style={{ padding: '0.375rem 0.875rem', fontSize: '0.875rem', fontWeight: 600, flexShrink: 0, textDecoration: 'none' }}
    >
      <Upload size={15} aria-hidden="true" style={{ verticalAlign: '-2px', marginRight: '0.375rem' }} />
      Upload
    </Link>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Layout
// ─────────────────────────────────────────────────────────────────────────────

// Lifecycle order: prepare (Toolkit) → collect (Review) → analyse (Insights).
// The conditional Realtime tab is inserted between Toolkit and Review, only
// for users with an active deployment.
//
// The labels are the words the home dashboard's three steps use (upload,
// review, insights), so the nav and the steps are one vocabulary. The routes
// keep their names: /annotations is the Review tab.
function tabLabel(Icon: LucideIcon, text: string) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
      <Icon size={15} aria-hidden="true" />
      {text}
    </span>
  )
}

const USER_TABS = [
  { id: 'toolkit',     label: tabLabel(Wrench, 'Toolkit'),     to: '/toolkit' },
  // Phase 3: a conditional Realtime tab is inserted here when the user has active
  // deployments reporting LoRaWAN heartbeats. Held back until the LoRaWAN
  // pipeline is live; the /field route renders a placeholder.
  { id: 'annotations', label: tabLabel(Tags, 'Review'),        to: '/annotations' },
  { id: 'insights',    label: tabLabel(ChartLine, 'Insights'), to: '/insights' },
] as const

function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout, isDemo } = useAuth()
  const location = useLocation()

  const { data: managedOrgs } = useQuery({
    queryKey: ['managedOrgs', user?.id],
    queryFn: async () => {
      if (!user) return []
      try {
        const res = await apiClient.get('/api/models/managed-orgs')
        return (res as any).data || []
      } catch { return [] }
    },
    enabled: !!user,
  })
  const isOrgManager = !!(managedOrgs && managedOrgs.length > 0)
  const isAdmin = useIsAdmin()

  // 📡 Field is a conditional tab: shown only when the user has active deployments
  // out in the field. Inserted between Toolkit and Annotations.
  const hasField = useHasActiveDeployments()
  const { unreadCount, items: notifications } = useNotifications()
  const navTabs = hasField
    ? [USER_TABS[0], { id: 'field', label: tabLabel(RadioTower, 'Realtime'), to: '/field' }, ...USER_TABS.slice(1)]
    : [...USER_TABS]

  // Resolve which top-level tab is active (handles nested routes too)
  const activeTab = navTabs.find(t => location.pathname.startsWith(t.to))?.id ?? null

  const tabStyle = (id: string): React.CSSProperties => ({
    display: 'flex', alignItems: 'center',
    padding: '0 1rem', height: '100%',
    borderBottom: activeTab === id ? '2px solid var(--primary)' : '2px solid transparent',
    color: activeTab === id ? 'var(--primary)' : 'var(--text-color)',
    fontWeight: activeTab === id ? 600 : 400,
    fontSize: '0.9rem', textDecoration: 'none',
    opacity: activeTab === id ? 1 : 0.72,
    transition: 'color 0.15s, border-color 0.15s',
    whiteSpace: 'nowrap',
  })

  return (
    <>
      <header style={{
        borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--surface)',
        height: '56px',
        display: 'flex',
        alignItems: 'stretch',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}>
        <div
          className="container"
          style={{ display: 'flex', alignItems: 'stretch', width: '100%', gap: 0 }}
        >
          {/* Logo */}
          <Link
            to="/"
            style={{
              textDecoration: 'none', color: 'var(--text-color)',
              fontWeight: 700, fontSize: '1rem',
              display: 'flex', alignItems: 'center',
              paddingRight: '1.5rem', whiteSpace: 'nowrap', flexShrink: 0,
            }}
          >
            Wildlife Watcher
          </Link>

          {/* Project selector — sits right of the logo; it scopes everything to its
              right, so the header reads "in these projects → these views". */}
          {user && (
            <div style={{ display: 'flex', alignItems: 'center', paddingRight: '1.5rem', flexShrink: 0 }}>
              <GlobalProjectSelector />
            </div>
          )}

          {/* 3-tab nav (signed-in only) */}
          {user && (
            <nav style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0 }}>
              {navTabs.map(tab => (
                <NavLink key={tab.id} to={tab.to} style={tabStyle(tab.id)}>
                  {tab.label}
                </NavLink>
              ))}
            </nav>
          )}

          {/* Flex spacer */}
          <div style={{ flex: 1 }} />

          {/* Right-side controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flexShrink: 0 }}>
            {/* Not on the login pages themselves: a Login button beside a login form is noise. */}
            {!user && !['/login', '/signup', '/reset-password'].includes(location.pathname) && (
              <Link to="/login" className="btn" style={{ padding: '0.375rem 0.875rem', textDecoration: 'none', fontSize: '0.875rem' }}>
                Login
              </Link>
            )}

            {user && (
              <>
                {!isDemo && <UploadNavButton />}
                <AccountMenu
                  email={user.email ?? ''}
                  isOrgManager={isOrgManager}
                  isAdmin={isAdmin === true}
                  onLogout={logout}
                  unreadCount={unreadCount}
                  recent={notifications}
                />
              </>
            )}
          </div>
        </div>
      </header>

      {/* Demo banner — the session is a real (read-only) login, so the rest of
          the app needs no demo-specific code paths. */}
      {isDemo && (
        <div style={{
          backgroundColor: 'rgba(76,175,80,0.12)', borderBottom: '1px solid var(--border)',
          padding: '0.5rem 0', fontSize: '0.8125rem', textAlign: 'center',
        }}>
          🔍 You're exploring a read-only demo with sample data.{' '}
          <button
            onClick={logout}
            style={{
              background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              color: 'var(--primary)', fontSize: 'inherit', fontWeight: 600, textDecoration: 'underline',
            }}
          >
            Exit demo
          </button>
          {' '}and create an account to work with your own data.
        </div>
      )}

      <main style={{ flex: 1, padding: '2rem 0' }}>
        <div className="container">
          <UploadQuotaBanner />
          {user && <InvitationsBanner />}
          {children}
        </div>
      </main>

      {/* Pulls iNaturalist community IDs once per session on login (no UI). */}
      <InatAutoSync />

      <footer style={{
        borderTop: '1px solid var(--border)',
        padding: '2.5rem 0',
        backgroundColor: 'var(--surface)',
        color: 'var(--text-muted)',
        textAlign: 'center',
      }}>
        <div className="container" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}>
          <div style={{ fontSize: '0.875rem' }}>
            &copy; {new Date().getFullYear()} Wildlife.ai
            {' | '}
            <Link to="/resources" style={{ color: 'inherit', textDecoration: 'underline' }}>Get started</Link>
            {' | '}
            <Link to="/guides" style={{ color: 'inherit', textDecoration: 'underline' }}>Guides</Link>
            {' | '}
            <Link to="/faq" style={{ color: 'inherit', textDecoration: 'underline' }}>FAQ</Link>
            {' | '}
            <Link to="/privacy" style={{ color: 'inherit', textDecoration: 'underline' }}>Privacy Policy</Link>
            {' | '}
            <Link to="/terms" style={{ color: 'inherit', textDecoration: 'underline' }}>Terms of Service</Link>
          </div>
        </div>
      </footer>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Routes
// ─────────────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ProjectSelectionProvider>
        <BrowserRouter>
          <DemoGuardProvider>
          <UploadProvider>
            <ProgressDock />
            <UndoToastHost />
            <Layout>
              <Routes>
              {/* Public */}
              <Route path="/"               element={<HomePage />} />
              <Route path="/login"          element={<LoginPage />} />
              <Route path="/signup"         element={<LoginPage mode="sign_up" />} />
              <Route path="/reset-password" element={<ResetPasswordPage />} />
              <Route path="/privacy"        element={<PrivacyPolicyPage />} />
              {/* Support page merged into /faq + /resources; redirect old bookmarks */}
              <Route path="/support"        element={<Navigate to="/faq" replace />} />
              <Route path="/terms"          element={<TermsOfServicePage />} />
              <Route path="/resources"      element={<ResourcesPage />} />
              <Route path="/faq"            element={<FaqPage />} />
              <Route path="/guides"         element={<Suspense fallback={<div style={{ padding: '2rem', textAlign: 'center', opacity: 0.5 }}>Loading…</div>}><GuidesPage /></Suspense>} />
              <Route path="/guides/:slug"   element={<Suspense fallback={<div style={{ padding: '2rem', textAlign: 'center', opacity: 0.5 }}>Loading…</div>}><GuideDetailPage /></Suspense>} />

              {/* Primary nav routes: Toolkit · Annotations · Insights (+ conditional Field later) */}
              <Route path="/toolkit"     element={<RequireAuth><ToolkitPage /></RequireAuth>} />
              <Route path="/field"       element={<RequireAuth><FieldPage /></RequireAuth>} />
              <Route path="/admin/usage" element={<RequireAuth><AdminUsagePage /></RequireAuth>} />
              <Route path="/admin/devices" element={<RequireAuth><AdminDevicesPage /></RequireAuth>} />
              <Route path="/annotations" element={<RequireAuth><AnnotationsPage /></RequireAuth>} />
              <Route path="/insights"    element={<RequireAuth><InsightsPage /></RequireAuth>} />

              {/* Personal surfaces (avatar menu) — fleshed out in later phases */}
              <Route path="/notifications" element={<RequireAuth><NotificationsPage /></RequireAuth>} />
              <Route path="/processing"    element={<RequireAuth><ProcessingHistoryPage /></RequireAuth>} />
              <Route path="/settings"      element={<RequireAuth><SettingsPage /></RequireAuth>} />

              {/* Legacy path redirects (query string preserved) */}
              <Route path="/results"  element={<RedirectTo to="/insights" />} />
              <Route path="/other"    element={<RedirectTo to="/toolkit" />} />
              <Route path="/my-data"  element={<RedirectTo to="/insights" />} />

              {/* Upload: the header button, Home and the three-step guide all land here */}
              <Route path="/upload-data"    element={<RequireAuth><RequireNotDemo feature="Uploading data"><UploadDataPage /></RequireNotDemo></RequireAuth>} />
              <Route path="/upload/logs"    element={<RequireAuth><UploadLogsPage /></RequireAuth>} />
              <Route path="/analyse-images" element={<Navigate to="/upload-data" replace />} />

              {/* Toolkit */}
              <Route path="/manifest"     element={<RequireAuth><ManifestPage /></RequireAuth>} />
              <Route path="/upload-model" element={<RequireAuth><RequireNotDemo feature="Uploading models"><UploadModelPage /></RequireNotDemo></RequireAuth>} />

              {/* Annotation workflow deep-links (preserved) */}
              <Route path="/explore/:deployment_id"  element={<RequireAuth><RedirectExploreToAnnotations /></RequireAuth>} />
              <Route path="/clusters/:deployment_id" element={<RequireAuth><ClusterReviewPage /></RequireAuth>} />
              <Route path="/umap/:deployment_id"     element={<RequireAuth><UmapExplorerPage /></RequireAuth>} />
              <Route path="/review/:deployment_id"   element={<RequireAuth><ReviewQueuePage /></RequireAuth>} />


              {/* Analysis / reporting deep-links (preserved) */}
              <Route path="/intelligence/:project_id" element={<RequireAuth><DatasetHealthPage /></RequireAuth>} />
              <Route path="/analysis/:deployment_id"  element={<RequireAuth><RedirectToReporting /></RequireAuth>} />
              <Route path="/reporting/:deployment_id" element={<RequireAuth><ReportingPage /></RequireAuth>} />

              {/* MyDataPage kept at a legacy path (internal; /my-data redirects above) */}
              <Route path="/my-data-legacy" element={<RequireAuth><MyDataPage /></RequireAuth>} />

              {/* 404 Page kept at the end of all routes due to wildcard '*' path. */}
              <Route path='*' element={<FourZeroFourPage />} />
            </Routes>
          </Layout>
          </UploadProvider>
          </DemoGuardProvider>
        </BrowserRouter>
      </ProjectSelectionProvider>
    </QueryClientProvider>
  )
}
