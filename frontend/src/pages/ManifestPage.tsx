import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { GenerateManifest } from '../components/toolkit/GenerateManifest'

export function ManifestPage() {
  return (
    <div>
      <Link to="/toolkit" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8125rem', color: 'var(--text-muted)', textDecoration: 'none', marginBottom: '0.75rem' }}>
        <ArrowLeft size={14} aria-hidden="true" /> Toolkit
      </Link>
      <GenerateManifest />
    </div>
  )
}
