import { useState } from 'react'
import { DownloadFirmware } from '../components/toolkit/DownloadFirmware'
import { UploadModel } from '../components/toolkit/UploadModel'
import { AnalyzeImages } from '../components/toolkit/AnalyzeImages'
import { GenerateManifest } from '../components/toolkit/GenerateManifest'

const TABS = [
  { id: 'download', label: '📥 Download Model', component: DownloadFirmware },
  { id: 'upload', label: '📤 Upload Model', component: UploadModel },
  { id: 'manifest', label: '⚙️ Generate Manifest', component: GenerateManifest },
  { id: 'analyze', label: '🖼️ Analyze Images', component: AnalyzeImages },
] as const

export function ToolkitPage() {
  const [activeTab, setActiveTab] = useState<string>('download')

  const ActiveComponent = TABS.find((t) => t.id === activeTab)?.component ?? DownloadFirmware

  return (
    <div>
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
