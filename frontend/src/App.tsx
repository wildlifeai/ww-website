import React, { useRef, useEffect, useState, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Link, NavLink, Navigate, useLocation, useParams } from 'react-router-dom'
import {
  Bell, Bot, Camera, ChartColumn, ChartLine, CircleHelp, History, Info, LogOut, RadioTower, Settings, Upload, Wrench,
  type LucideIcon,
} from 'lucide-react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { activeNavTab, type NavTabId } from './lib/navTabs'
import { useAuth } from './hooks/useAuth'
import { ProjectSelectionProvider } from './hooks/useProjectSelection'
import { GlobalProjectSelector } from './components/common/GlobalProjectSelector'
import { useHasRealtimeData } from './hooks/useHasRealtimeData'
import { useNotifications, type AppNotification } from './hooks/useNotifications'
import { InatAutoSync } from './components/settings/InatAutoSync'
import { useIsAdmin } from './hooks/useIsAdmin'
import { useProjectRoles } from './hooks/useProjectRoles'
import { useWritableProjects } from './hooks/useWritableProjects'
import { UploadQuotaBanner } from './components/UploadQuotaBanner'
import { InvitationsBanner } from './components/InvitationsBanner'
import { Banner, BannerSlot } from './components/common/Banner'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { PrivacyPolicyPage } from './pages/PrivacyPolicyPage'
import { TermsOfServicePage } from './pages/TermsOfServicePage'
import { ResourcesPage } from './pages/ResourcesPage'
import { FaqPage } from './pages/FaqPage'
import { GuidesPage } from './pages/GuidesPage'
import { FourZeroFourPage } from './pages/FourZeroFourPage'
import { UploadProvider } from './contexts/UploadContext'
import { DemoGuardProvider, RequireNotDemo } from './components/common/DemoGuard'
import { UndoToastHost } from './components/common/UndoToast'
import './styles/index.css'

// The public pages are in the main chunk, so they render without a "Loading…" first (a layout
// shift Lighthouse counts, #228). A guide is lazy so the markdown renderer stays out of it, and
// every signed-in page is lazy so a visitor does not download them.
const GuideDetailPage = React.lazy(() => import('./pages/GuideDetailPage'))

function lazyPage<M extends Record<K, React.ComponentType>, K extends keyof M>(load: () => Promise<M>, name: K) {
  return React.lazy(() => load().then(m => ({ default: m[name] })))
}

// The upload dock floats over the page, so loading it after the first render shifts nothing.
const ProgressDock = lazyPage(() => import('./components/upload/ProgressDock'), 'ProgressDock')
const AdminUsagePage = lazyPage(() => import('./pages/AdminUsagePage'), 'AdminUsagePage')
const AdminDevicesPage = lazyPage(() => import('./pages/AdminDevicesPage'), 'AdminDevicesPage')
const ManifestPage = lazyPage(() => import('./pages/ManifestPage'), 'ManifestPage')
const UploadModelPage = lazyPage(() => import('./pages/UploadModelPage'), 'UploadModelPage')
const UploadDataPage = lazyPage(() => import('./pages/UploadDataPage'), 'UploadDataPage')
const ClusterReviewPage = lazyPage(() => import('./pages/ClusterReviewPage'), 'ClusterReviewPage')
const UmapExplorerPage = lazyPage(() => import('./pages/UmapExplorerPage'), 'UmapExplorerPage')
const ReviewQueuePage = lazyPage(() => import('./pages/ReviewQueuePage'), 'ReviewQueuePage')
const DatasetHealthPage = lazyPage(() => import('./pages/DatasetHealthPage'), 'DatasetHealthPage')
const AnnotationsPage = lazyPage(() => import('./pages/AnnotationsPage'), 'AnnotationsPage')
const InsightsPage = lazyPage(() => import('./pages/InsightsPage'), 'InsightsPage')
const ToolkitPage = lazyPage(() => import('./pages/ToolkitPage'), 'ToolkitPage')
const FieldPage = lazyPage(() => import('./pages/FieldPage'), 'FieldPage')
const NotificationsPage = lazyPage(() => import('./pages/NotificationsPage'), 'NotificationsPage')
const ProcessingHistoryPage = lazyPage(() => import('./pages/ProcessingHistoryPage'), 'ProcessingHistoryPage')
const SettingsPage = lazyPage(() => import('./pages/SettingsPage'), 'SettingsPage')

// ─────────────────────────────────────────────────────────────────────────────
// Auth guard
// ─────────────────────────────────────────────────────────────────────────────

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading…</div>
  // LoginPage returns to state.from after signing in, so a deep link survives the login.
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return <>{children}</>
}

// Legacy-path redirect that preserves the query string (e.g. /results?tab=projects
// → /insights?tab=projects), so old bookmarks keep their sub-tab.
function RedirectTo({ to }: { to: string }) {
  const { search } = useLocation()
  return <Navigate to={`${to}${search}`} replace />
}

// A deployment's results live on Insights > Reports (UI audit decision 5). The old
// /reporting/:id and /analysis/:id links redirect there, so bookmarks keep working.
function RedirectToReports() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  return <Navigate to={`/insights?tab=reports&deployment=${encodeURIComponent(deployment_id ?? '')}`} replace />
}

// The old /explore image grid is now redundant — the Annotations page does the
// grid, clustering, bulk cluster-confirm, and similar-image search. Redirect.
function RedirectExploreToAnnotations() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  return <Navigate to={`/annotations?deployment=${deployment_id}`} replace />
}

// ─────────────────────────────────────────────────────────────────────────────
// AccountMenu: the avatar dropdown
// ─────────────────────────────────────────────────────────────────────────────

const MENU_ITEM: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  padding: '0.45rem 0.875rem', fontSize: '0.8125rem',
  border: 'none', backgroundColor: 'transparent',
  color: 'var(--text-color)', cursor: 'pointer', width: '100%', textAlign: 'left',
  textDecoration: 'none',
}

function MenuLink({ to, icon: Icon, onClose, children }: { to: string; icon: LucideIcon; onClose: () => void; children: React.ReactNode }) {
  return (
    <Link to={to} onClick={onClose} className="menu-item" style={MENU_ITEM}>
      <Icon size={15} aria-hidden="true" color="var(--text-muted)" />
      {children}
    </Link>
  )
}

const MENU_DIVIDER = <div style={{ height: '1px', backgroundColor: 'var(--border)', margin: '0.25rem 0' }} />

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
  const close = () => setOpen(false)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(v => !v)}
        title={email}
        aria-label={`Account: ${email}`}
        aria-haspopup="menu"
        aria-expanded={open}
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
        <div role="menu" style={{
          position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 200,
          backgroundColor: 'var(--bg-color)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
          minWidth: '240px', padding: '0.25rem 0',
        }}>
          {/* Signed-in identity */}
          <div style={{ padding: '0.4rem 0.875rem 0.5rem', borderBottom: '1px solid var(--border)', marginBottom: '0.25rem' }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Signed in as</div>
            <div style={{ fontSize: '0.8125rem', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{email}</div>
          </div>
          {/* Recent notifications preview */}
          {recent.length > 0 && (
            <>
              <div style={{ padding: '0.35rem 0.875rem 0.2rem', fontSize: '0.7rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                Recent
              </div>
              {recent.slice(0, 5).map(n => (
                <Link
                  key={n.id}
                  to="/notifications"
                  onClick={close}
                  className="menu-item"
                  style={{ ...MENU_ITEM, alignItems: 'flex-start', flexDirection: 'column', gap: '0.1rem', color: n.read_at ? 'var(--text-muted)' : 'var(--text-color)' }}
                >
                  <span style={{ fontWeight: n.read_at ? 500 : 700, fontSize: '0.78rem', whiteSpace: 'normal' }}>
                    {!n.read_at && <span style={{ color: '#ef4444', marginRight: 4 }}>•</span>}{n.title}
                  </span>
                </Link>
              ))}
              {MENU_DIVIDER}
            </>
          )}
          <MenuLink to="/notifications" icon={Bell} onClose={close}>
            Notifications{unreadCount > 0 ? ` (${unreadCount})` : ''}
          </MenuLink>
          <MenuLink to="/processing" icon={History} onClose={close}>Processing history</MenuLink>
          <MenuLink to="/settings" icon={Settings} onClose={close}>Settings</MenuLink>
          {isAdmin && <MenuLink to="/admin/usage" icon={ChartColumn} onClose={close}>User usage</MenuLink>}
          {isAdmin && <MenuLink to="/admin/devices" icon={RadioTower} onClose={close}>Devices</MenuLink>}
          {isOrgManager && <MenuLink to="/upload-model" icon={Bot} onClose={close}>Upload model</MenuLink>}
          <MenuLink to="/guides" icon={CircleHelp} onClose={close}>Help</MenuLink>
          {MENU_DIVIDER}
          <button onClick={() => { close(); onLogout() }} className="menu-item" style={MENU_ITEM}>
            <LogOut size={15} aria-hidden="true" color="var(--text-muted)" />
            Log out
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
      aria-label="Upload"
      style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', padding: '0.375rem 0.875rem', fontSize: '0.875rem', fontWeight: 600, flexShrink: 0, textDecoration: 'none' }}
    >
      <Upload size={15} aria-hidden="true" />
      <span className="hdr-text">Upload</span>
    </Link>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Layout
// ─────────────────────────────────────────────────────────────────────────────

// Lifecycle order: prepare (Toolkit), monitor (Realtime), collect (Review), analyse (Insights).
// Realtime shows only when one of the user's projects has a device that sends or has sent
// LoRaWAN data (UI audit decision 7, useHasRealtimeData); otherwise it is not in the header.
//
// The labels are the words the home dashboard's three steps use (upload,
// review, insights), so the nav and the steps are one vocabulary. The routes
// keep their names: /annotations is the Media tab. Below 900 px a tab shows its icon only.
const USER_TABS: { id: NavTabId; icon: LucideIcon; text: string; to: string }[] = [
  { id: 'toolkit',     icon: Wrench,     text: 'Toolkit',  to: '/toolkit' },
  { id: 'field',       icon: RadioTower, text: 'Realtime', to: '/field' },
  { id: 'annotations', icon: Camera,     text: 'Media',    to: '/annotations' },
  { id: 'insights',    icon: ChartLine,  text: 'Insights', to: '/insights' },
]

function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout, isDemo } = useAuth()
  const location = useLocation()
  const roles = useProjectRoles()
  const isAdmin = useIsAdmin()
  const { canWriteAny } = useWritableProjects()
  const hasRealtime = useHasRealtimeData()
  const { unreadCount, items: notifications } = useNotifications()
  const navTabs = USER_TABS.filter(t => t.id !== 'field' || hasRealtime)

  // A deep route (a cluster review, a report, the SD card builder) lights its parent tab.
  const activeTab = activeNavTab(location.pathname)

  const tabStyle = (id: string): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: '0.4rem', height: '100%',
    borderBottom: activeTab === id ? '2px solid var(--primary)' : '2px solid transparent',
    color: activeTab === id ? 'var(--primary)' : 'var(--text-muted)',
    fontWeight: activeTab === id ? 600 : 400,
    fontSize: '0.9rem', textDecoration: 'none',
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
          {/* Logo: the mark alone below 1100 px, where a long project name and the Realtime tab
              would otherwise push the header past the window */}
          <Link
            to="/"
            aria-label="Wildlife Watcher home"
            className="hdr-gap"
            style={{
              textDecoration: 'none', color: 'var(--text-color)',
              fontWeight: 700, fontSize: '1rem',
              display: 'flex', alignItems: 'center', gap: '0.4rem',
              whiteSpace: 'nowrap', flexShrink: 0,
            }}
          >
            <img src="/favicon.png" alt="" width={22} height={22} style={{ display: 'block' }} />
            {/* Signed out, the header has room for the name at any width. */}
            <span className={user ? 'hdr-logo' : undefined}>Wildlife Watcher</span>
          </Link>

          {/* Project selector: sits right of the logo; it scopes everything to its
              right, so the header reads "in these projects, these views". */}
          {user && (
            <div className="hdr-gap" style={{ display: 'flex', alignItems: 'center', minWidth: 0 }}>
              <GlobalProjectSelector />
            </div>
          )}

          {/* Tab nav (signed-in only) */}
          {user && (
            <nav style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0 }}>
              {navTabs.map(({ id, icon: Icon, text, to }) => (
                <NavLink key={id} to={to} className="hdr-tab" style={tabStyle(id)} aria-label={text} title={text}>
                  <Icon size={15} aria-hidden="true" />
                  <span className="hdr-text">{text}</span>
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
                {/* Hidden for a user who can write to no project (decision 1), the demo included. */}
                {canWriteAny && <UploadNavButton />}
                <AccountMenu
                  email={user.email ?? ''}
                  isOrgManager={!roles.loading && roles.managesAnyOrg}
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

      <main className="app-main" style={{ flex: 1 }}>
        <div className="container">
          {/* One place for notices above the page (UI audit 01 A5). */}
          <BannerSlot>
            {user && <InvitationsBanner />}
            <UploadQuotaBanner />
            {/* The demo session is a real (read-only) login, so the rest of the app needs no
                demo-specific code paths. */}
            {isDemo && (
              <Banner icon={Info}>
                You&apos;re exploring a read-only demo with sample data.{' '}
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
              </Banner>
            )}
          </BannerSlot>
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
          {/* Signed in, the prototype notice is this one line; the public home page has the banner. */}
          {user && (
            <div style={{ fontSize: '0.8125rem' }}>
              Wildlife Watcher is a prototype and changes week to week. Tell us what breaks at{' '}
              <a href="mailto:contact@wildlife.ai?subject=Wildlife%20Watcher%20feedback" style={{ color: 'inherit' }}>contact@wildlife.ai</a>.
            </div>
          )}
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
            <Suspense fallback={null}><ProgressDock /></Suspense>
            <UndoToastHost />
            <Layout>
              {/* For the lazy pages: a guide and every signed-in page */}
              <Suspense fallback={<div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</div>}>
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
              <Route path="/guides"         element={<GuidesPage />} />
              <Route path="/guides/:slug"   element={<GuideDetailPage />} />

              {/* Primary nav routes: Toolkit, Realtime (/field, shown with LoRaWAN data), Review, Insights */}
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
              {/* Upload logs were retired for Processing history (decision 3) */}
              <Route path="/upload/logs"    element={<Navigate to="/processing" replace />} />
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
              <Route path="/analysis/:deployment_id"  element={<RedirectToReports />} />
              <Route path="/reporting/:deployment_id" element={<RedirectToReports />} />

              {/* The legacy My Data page was retired (decision 3); its path goes where /my-data does */}
              <Route path="/my-data-legacy" element={<RedirectTo to="/insights" />} />

              {/* 404 Page kept at the end of all routes due to wildcard '*' path. */}
              <Route path='*' element={<FourZeroFourPage />} />
            </Routes>
              </Suspense>
          </Layout>
          </UploadProvider>
          </DemoGuardProvider>
        </BrowserRouter>
      </ProjectSelectionProvider>
    </QueryClientProvider>
  )
}
