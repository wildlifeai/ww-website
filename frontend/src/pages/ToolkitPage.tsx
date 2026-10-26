// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ToolkitPage: /toolkit
// Occasional, task-flavoured tools: the live view, AI models, iNaturalist, exports, and Prepare SD
// card last. Each card shows only to a role that can use it (UI audit decision 1): Prepare SD card
// needs write access to a project, the model and GBIF cards are for organisation managers, and
// the export needs read access only (ww-backend #331), so viewers and the demo export too.
// (Monitoring lives in Realtime; configuration in Settings.)
import { Bot, Download, Globe, MemoryStick, Package, ScanEye, Wrench } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useProjectRoles } from '../hooks/useProjectRoles'
import { useWritableProjects } from '../hooks/useWritableProjects'
import { useCamtrapExport } from '../hooks/useCamtrapExport'
import { CamtrapExportStatus } from '../components/data/CamtrapExportStatus'
import { INaturalistPanel } from '../components/toolkit/INaturalistPanel'
import { ToolCard } from '../components/toolkit/ToolCard'

const LINK_BUTTON: React.CSSProperties = { textDecoration: 'none', width: 'fit-content', display: 'inline-block' }

export function ToolkitPage() {
  const { projects, selectedProjectIds } = useProjectSelection()
  const roles = useProjectRoles()
  const { canWriteAny } = useWritableProjects()
  const camtrapExport = useCamtrapExport()
  const isOrgManager = !roles.loading && roles.managesAnyOrg

  const oneSelected = selectedProjectIds.length === 1
  const canViewAny = !roles.loading && projects.some(p => roles.canView(p.id))
  const canExport = !roles.loading && oneSelected && roles.canView(selectedProjectIds[0])

  return (
    <div>
      <h2 style={{ margin: '0 0 0.375rem 0', display: 'flex', alignItems: 'center', gap: '0.5rem' }}><Wrench size={22} color="var(--primary)" aria-hidden="true" />Toolkit</h2>
      <p style={{ margin: '0 0 2rem 0', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
        Prepare cameras, connect integrations and export your data.
      </p>

      <div style={{ maxWidth: '720px', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <ToolCard
          icon={ScanEye}
          title="Motion live view"
          description="See a camera's live picture over its USB cable, where it detects motion and whether that would wake it, and tune its motion settings."
        >
          <Link to="/live-view" className="btn" style={LINK_BUTTON}>Open live view</Link>
        </ToolCard>

        {isOrgManager && (
          <ToolCard
            icon={Bot}
            title="Upload AI model"
            description="Upload an Edge Impulse ZIP, run Vela optimisation and register the model so projects can select it."
          >
            <Link to="/upload-model" className="btn" style={LINK_BUTTON}>Upload a model</Link>
          </ToolCard>
        )}

        <INaturalistPanel />

        {canViewAny && (
          <ToolCard
            icon={Package}
            title="Export dataset for R"
            description="Download the selected project as a CamtrapDP package (ZIP) with its original photos, for the camtrapdp R package or any tool that reads it."
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {!canExport && (
                <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', margin: 0 }}>
                  {oneSelected ? 'You need a role on this project to export it.' : 'Select one project in the header to export it.'}
                </p>
              )}
              <CamtrapExportStatus state={camtrapExport} />
              <button
                id="download-camtrapdp-btn"
                className="btn"
                onClick={() => camtrapExport.start(selectedProjectIds)}
                disabled={!canExport || camtrapExport.running}
                style={{ opacity: !canExport ? 0.45 : 1, width: 'fit-content', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                title={!oneSelected ? 'Select exactly one project first' : undefined}
              >
                <Download size={15} aria-hidden="true" />
                {camtrapExport.running ? 'Exporting…' : 'Download CamtrapDP'}
              </button>
            </div>
          </ToolCard>
        )}

        {isOrgManager && (
          <ToolCard
            icon={Globe}
            title="Publish to GBIF"
            description="Publish a project's observations to the Global Biodiversity Information Facility as a Darwin Core Archive."
            comingSoon
          />
        )}

        {canWriteAny && (
          <ToolCard
            icon={MemoryStick}
            title="Prepare SD card"
            description="Build a MANIFEST.zip with the camera software and your project's AI model, to copy to an SD card."
          >
            <Link to="/manifest" className="btn" style={LINK_BUTTON}>Prepare an SD card</Link>
          </ToolCard>
        )}
      </div>
    </div>
  )
}
