import React from 'react'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import { useAuth } from './hooks/useAuth'
import './styles/index.css'

function Layout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()

  return (
    <>
      <header style={{ 
        borderBottom: '1px solid var(--border)', 
        backgroundColor: 'var(--surface)',
        padding: '1rem' 
      }}>
        <div className="container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Link to="/" style={{ textDecoration: 'none', color: 'var(--text-color)', fontWeight: 'bold', fontSize: '1.25rem' }}>
            Wildlife Watcher V2
          </Link>
          <nav style={{ display: 'flex', gap: '1rem' }}>
            <Link to="/toolkit" style={{ textDecoration: 'none', color: 'var(--text-color)' }}>Toolkit</Link>
            {user ? (
              <span style={{ fontSize: '0.875rem', opacity: 0.8 }}>{user.email}</span>
            ) : (
              <button className="btn" style={{ padding: '0.25rem 0.75rem' }}>Login</button>
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
      <Link to="/toolkit" className="btn" style={{ fontSize: '1.125rem', padding: '0.75rem 2rem' }}>
        Open Toolkit
      </Link>
    </div>
  )
}

function ToolkitDummy() {
  return (
    <div>
      <h2>Toolkit</h2>
      <p>The Toolkit interface will go here (Download Firmware, Upload Model, EXIF Parser).</p>
      
      <div className="card" style={{ marginTop: '2rem' }}>
        <h3>Scaffolding Status</h3>
        <ul style={{ paddingLeft: '1.5rem', marginTop: '1rem' }}>
          <li>✅ React + Vite + TypeScript</li>
          <li>✅ TanStack Query setup</li>
          <li>✅ React Router setup</li>
          <li>✅ Supabase + useAuth Hook</li>
          <li>✅ Global CSS Variables</li>
        </ul>
      </div>
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
            <Route path="/toolkit" element={<ToolkitDummy />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
