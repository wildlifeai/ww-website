import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { AnalyseImages } from '../components/toolkit/AnalyseImages'

export function HomePage() {
  const { user, loading } = useAuth()

  return (
    <div>
      <div style={{ textAlign: 'center', maxWidth: '800px', margin: '0 auto', padding: '0 0 3rem 0' }}>
        <h1 style={{ fontSize: '3rem', color: 'var(--primary)', marginBottom: '1rem' }}>Monitor wildlife the right way!</h1>
        <p style={{ fontSize: '1.25rem', opacity: 0.8 }}>
          Analyse here the photos from your Wildlife Watchers, upload new models, visualise your data and get the devices ready to set them up in the field.
        </p>

        {!loading && !user && (
          <Link
            to="/login"
            className="btn"
            id="hero-login-button"
            style={{
              display: 'inline-block',
              marginTop: '2rem',
              padding: '0.875rem 2.5rem',
              fontSize: '1.125rem',
              fontWeight: 600,
              textDecoration: 'none',
              borderRadius: 'var(--radius)',
              boxShadow: '0 4px 14px rgba(0,110,28,0.3)',
              transition: 'transform 0.2s, box-shadow 0.2s',
            }}
          >
            Log in to get started
          </Link>
        )}
      </div>

      {user && <AnalyseImages />}
    </div>
  )
}
