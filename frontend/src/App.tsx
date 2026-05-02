import React from 'react'
import { BrowserRouter, Routes, Route, Link, Navigate } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { useAuth } from './hooks/useAuth'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { MyDataPage } from './pages/MyDataPage'
import { ManifestPage } from './pages/ManifestPage'
import { UploadModelPage } from './pages/UploadModelPage'
import { AnalyseImagesPage } from './pages/AnalyseImagesPage'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from './lib/apiClient'
import './styles/index.css'

/** Redirects to /login if the user is not authenticated */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading…</div>
  if (!user) return <Navigate to="/login" replace />

  return <>{children}</>
}

function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth()

  const { data: managedOrgs } = useQuery({
    queryKey: ['managedOrgs', user?.id],
    queryFn: async () => {
      if (!user) return []
      try {
        const res = await apiClient.get('/api/models/managed-orgs')
        return (res as any).data || []
      } catch (e) {
        return []
      }
    },
    enabled: !!user,
  })

  const isOrgManager = managedOrgs && managedOrgs.length > 0

  return (
    <>
      <header style={{
        borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--surface)',
        padding: '1rem'
      }}>
        <div className="container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Link to="/" style={{ textDecoration: 'none', color: 'var(--text-color)', fontWeight: 'bold', fontSize: '1.25rem' }}>
            Wildlife Watcher Web
          </Link>
          <nav style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            {user && (
              <>
                <Link to="/my-data" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>My Data</Link>
                <Link to="/analyse-images" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>Analyse Images</Link>
                <Link to="/manifest" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>Prepare SD Card</Link>
                {isOrgManager && (
                  <Link to="/upload-model" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>Upload Model</Link>
                )}
              </>
            )}
            {user ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontSize: '0.875rem', opacity: 0.8 }}>{user.email}</span>
                <button className="btn" onClick={logout} style={{ padding: '0.25rem 0.75rem', backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-color)' }}>Logout</button>
              </div>
            ) : (
              <Link to="/login" className="btn" style={{ padding: '0.25rem 0.75rem', textDecoration: 'none' }}>Login</Link>
            )}
          </nav>
        </div>
      </header>

      <main style={{ flex: 1, padding: '2rem 0' }}>
        <div className="container">
          {children}
        </div>
      </main>

      <footer style={{
        borderTop: '1px solid var(--border)',
        padding: '2rem 0',
        backgroundColor: 'var(--surface)',
        color: 'var(--text-color)',
        textAlign: 'center',
        opacity: 0.8,
        fontSize: '0.875rem'
      }}>
        <div className="container">
          &copy; {new Date().getFullYear()} Wildlife.ai
        </div>
      </footer>
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/my-data" element={<RequireAuth><MyDataPage /></RequireAuth>} />
            <Route path="/analyse-images" element={<RequireAuth><AnalyseImagesPage /></RequireAuth>} />
            <Route path="/manifest" element={<RequireAuth><ManifestPage /></RequireAuth>} />
            <Route path="/upload-model" element={<RequireAuth><UploadModelPage /></RequireAuth>} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
