import React from 'react'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { useAuth } from './hooks/useAuth'
import { ToolkitPage } from './pages/ToolkitPage'
import './styles/index.css'

function Layout({ children }: { children: React.ReactNode }) {
  const { user, login, logout } = useAuth()

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
            <Link to="/toolkit" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>Toolkit</Link>
            {user ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontSize: '0.875rem', opacity: 0.8 }}>{user.email}</span>
                <button className="btn" onClick={logout} style={{ padding: '0.25rem 0.75rem', backgroundColor: 'transparent', border: '1px solid var(--border)' }}>Logout</button>
              </div>
            ) : (
              <button className="btn" onClick={login} style={{ padding: '0.25rem 0.75rem' }}>Login</button>
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

function LandingPage() {
  return (
    <div style={{ textAlign: 'center', maxWidth: '800px', margin: '0 auto', padding: '4rem 0' }}>
      <h1 style={{ fontSize: '3rem', color: 'var(--primary)', marginBottom: '1rem' }}>Monitor Wildlife Globally</h1>
      <p style={{ fontSize: '1.25rem', opacity: 0.8, marginBottom: '2rem' }}>
        Deploy robust camera trap models, monitor real-time telemetry via LoRaWAN, and generate firmware config—all in one place.
      </p>
      <Link to="/toolkit" className="btn" style={{ fontSize: '1.125rem', padding: '0.75rem 2rem', textDecoration: 'none' }}>
        Open Toolkit
      </Link>
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/toolkit" element={<ToolkitPage />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
