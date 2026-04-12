import { useState } from 'react'
import { UploadModel } from '../components/toolkit/UploadModel'
import { AnalyseImages } from '../components/toolkit/AnalyseImages'
import { GenerateManifest } from '../components/toolkit/GenerateManifest'

const TABS = [
  { id: 'analyse', label: '🖼️ Analyse Images', component: AnalyseImages },
  { id: 'manifest', label: '⚙️ Generate Manifest', component: GenerateManifest },
  { id: 'upload', label: '📤 Upload Model', component: UploadModel },
] as const

export function HomePage() {
  const [activeTab, setActiveTab] = useState<string>('analyse')

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? AnalyseImages


  return (
    <div>
      <div style={{ textAlign: 'center', maxWidth: '800px', margin: '0 auto', padding: '0 0 4rem 0' }}>
        <h1 style={{ fontSize: '3rem', color: 'var(--primary)', marginBottom: '1rem' }}>Monitor Wildlife Globally</h1>
        <p style={{ fontSize: '1.25rem', opacity: 0.8 }}>
          Deploy robust camera trap models, monitor real-time telemetry via LoRaWAN, and generate firmware config—all in one place.
        </p>
      </div>

      <h2 style={{ marginBottom: '1.5rem' }}>Toolkit</h2>

      {/* Tab bar */}
      <div style={{
        display: 'flex',
        gap: '0',
        borderBottom: '2px solid var(--border)',
        marginBottom: '2rem',
        overflowX: 'auto',
      }}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '0.75rem 1.25rem',
              border: 'none',
              borderBottom: activeTab === tab.id ? '2px solid var(--primary)' : '2px solid transparent',
              backgroundColor: 'transparent',
              color: activeTab === tab.id ? 'var(--primary)' : 'var(--text-color)',
              fontWeight: activeTab === tab.id ? 600 : 400,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              transition: 'color 0.2s, border-color 0.2s',
              marginBottom: '-2px',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Active tab content */}
      <ActiveComponent />
    </div>
  )
}
