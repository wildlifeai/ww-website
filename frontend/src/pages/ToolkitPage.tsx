// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ToolkitPage — /toolkit
// Occasional, task-flavoured tools, grouped: Camera prep · AI models ·
// Integrations · Exports. (Monitoring lives in Field; configuration in Settings.)
import { Wrench } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { apiClient } from '../lib/apiClient'
import { useCamtrapExport } from '../hooks/useCamtrapExport'
import { CamtrapExportStatus } from '../components/data/CamtrapExportStatus'
import { INaturalistPanel } from '../components/toolkit/INaturalistPanel'

function Section({ icon, title, description, children, comingSoon = false }: {
  icon: string; title: string; description: string; children?: React.ReactNode; comingSoon?: boolean
}) {
  return (
    <div style={{
      backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
      padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem', opacity: comingSoon ? 0.6 : 1,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.875rem' }}>
        <div style={{
          width: 44, height: 44, borderRadius: 10, flexShrink: 0,
          background: 'linear-gradient(135deg,rgba(76,175,80,0.25),rgba(76,175,80,0.07))',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.375rem',
        }}>{icon}</div>
        <div>
          <div style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.25rem' }}>
            {title}
            {comingSoon && (
              <span style={{
                marginLeft: '0.5rem', fontSize: '0.6875rem', fontWeight: 500, padding: '0.15rem 0.5rem',
                borderRadius: '12px', border: '1px solid var(--border)', opacity: 0.7,
              }}>coming soon</span>
            )}
          </div>
          <p style={{ margin: 0, fontSize: '0.875rem', opacity: 0.7, lineHeight: 1.5 }}>{description}</p>
        </div>
      </div>
      {children && <div>{children}</div>}
    </div>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: '2rem' }}>
      <h3 style={{
        margin: '0 0 0.75rem 0', fontSize: '0.8rem', fontWeight: 700, letterSpacing: '0.04em',
        textTransform: 'uppercase', opacity: 0.55,
      }}>{title}</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>{children}</div>
    </section>
  )
}

export function ToolkitPage() {
  const { user } = useAuth()
  const { selectedProjectIds } = useProjectSelection()
  const camtrapExport = useCamtrapExport()

  const { data: managedOrgs } = useQuery({
    queryKey: ['managedOrgs', user?.id],
    queryFn: async () => {
      if (!user) return []
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      try { const res = await apiClient.get('/api/models/managed-orgs'); return (res as any).data || [] } catch { return [] }
    },
    enabled: !!user,
  })
  const isOrgManager = managedOrgs && managedOrgs.length > 0

  const canExport = selectedProjectIds.length === 1

  return (
    <div>
      <h2 style={{ margin: '0 0 0.375rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Wrench size={22} color="var(--primary)" aria-hidden="true" />Toolkit</h2>
      <p style={{ margin: '0 0 2rem 0', opacity: 0.65, fontSize: '0.9rem' }}>
        Prepare cameras, manage AI models, connect integrations, and export your data.
      </p>

      <div style={{ maxWidth: '720px' }}>
        <Group title="Camera prep">
          <Section
            icon="💾"
            title="Prepare SD card"
            description="Generate a MANIFEST.zip that bundles the camera configuration and the latest AI model binary, ready to write to an SD card for field deployment."
          >
            <Link to="/manifest" className="btn" style={{ textDecoration: 'none', width: 'fit-content', display: 'inline-block' }}>
              Open SD card preparation →
            </Link>
          </Section>
        </Group>

        <Group title="AI models">
          {isOrgManager ? (
            <Section
              icon="🤖"
              title="Upload AI model"
              description="Upload an Edge Impulse ZIP, run Vela optimisation, and register the new model in the system so projects can select it."
            >
              <Link to="/upload-model" className="btn" style={{ textDecoration: 'none', width: 'fit-content', display: 'inline-block' }}>
                Open model upload →
              </Link>
            </Section>
          ) : (
            <Section
              icon="🤖"
              title="AI models"
              description="Uploading and managing embedded AI models requires the Organisation Manager role. Ask an org admin for access."
            />
          )}
        </Group>

        <Group title="Integrations">
          <Section
            icon="🕊"
            title="Connect iNaturalist"
            description="Link your personal iNaturalist account to publish reviewed camera-trap observations from the Annotations tab and sync community identifications back into Wildlife Watcher."
          >
            <INaturalistPanel />
          </Section>
        </Group>

        <Group title="Exports">
          <Section
            icon="📦"
            title="Export dataset for R"
            description="Download the selected project as a CamtrapDP package (ZIP), with its original photos. Open it in the camtrapdp R package or any tool that reads Camera Trap Data Packages."
          >
            {!canExport && (
              <p style={{ fontSize: '0.8125rem', opacity: 0.65, margin: 0 }}>
                Select exactly one project from the Projects selector at the top of the screen to enable export.
              </p>
            )}
            <CamtrapExportStatus state={camtrapExport} />
            <button
              id="download-camtrapdp-btn"
              className="btn"
              onClick={() => camtrapExport.start(selectedProjectIds)}
              disabled={!canExport || camtrapExport.running}
              style={{ opacity: !canExport ? 0.45 : 1, width: 'fit-content' }}
              title={!canExport ? 'Select exactly one project first' : undefined}
            >
              {camtrapExport.running ? '⏳ Exporting…' : '📦 Download CamtrapDP'}
            </button>
          </Section>

          <Section
            icon="🌍"
            title="Publish to GBIF"
            description="Publish observations from a project or deployment to the Global Biodiversity Information Facility (GBIF) as a Darwin Core Archive."
            comingSoon
          />
        </Group>
      </div>
    </div>
  )
}
