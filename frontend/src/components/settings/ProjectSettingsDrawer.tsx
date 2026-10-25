// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// ProjectSettingsDrawer: one project's settings in a right-hand slide-over, opened by clicking its
// row on Settings (UI audit 05 #9). The header names the project. The tabs follow the user's role
// (decisions 1 and 8): Details and Delete for project admins; Members for everyone who can see
// the project, with invite and remove for admins; Defaults for everyone, read-only below admin;
// Notifications for members and admins, since saving a rule needs project_member.
import { useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { useProjectRoles } from '../../hooks/useProjectRoles'
import { useSaveStatus } from '../../hooks/useSaveStatus'
import type { ProjectDetails } from '../../lib/projectDetails'
import { ProjectMembersPanel } from '../data/ProjectMembersPanel'
import { NotificationRulesPanel } from './NotificationRulesPanel'
import { ProjectDefaultsPanel } from './ProjectDefaultsPanel'
import { ProjectDetailsPanel } from './ProjectDetailsPanel'
import { SaveStatus } from './SaveStatus'

type Tab = 'details' | 'members' | 'defaults' | 'notifications'

const TAB_LABEL: Record<Tab, string> = {
  details: 'Details', members: 'Members', defaults: 'Defaults', notifications: 'Notifications',
}

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

export function ProjectSettingsDrawer({ project, onClose, onDetailsSaved, onDelete }: {
  project: { id: string; name: string }
  onClose: () => void
  /** The stored row after a Details save, so the page can show the new name. */
  onDetailsSaved: (saved: ProjectDetails) => void
  /** Opens the delete confirmation; offered to project admins only. */
  onDelete: () => void
}) {
  const roles = useProjectRoles()
  const admin = roles.isProjectAdmin(project.id)
  const tabs: Tab[] = roles.loading ? [] : [
    ...(admin ? ['details' as const] : []),
    'members',
    'defaults',
    ...(roles.canWrite(project.id) ? ['notifications' as const] : []),
  ]
  const [picked, setPicked] = useState<Tab | null>(null)
  const tab = picked && tabs.includes(picked) ? picked : tabs[0]
  // One confirmation for the tabs that save on change (Defaults, Notifications).
  const save = useSaveStatus()

  const choose = (t: Tab) => {
    setPicked(t)
    save.reset()
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 200, display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end' }}>
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={onClose}
        style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.35)', border: 'none', padding: 0, cursor: 'default' }}
      />
      {/* Not showModal(): the delete confirmation renders outside this element and must stay above it. */}
      <dialog
        open
        aria-label={`Settings for ${project.name}`}
        style={{
          position: 'relative', zIndex: 1, width: 'min(560px, 100vw)', height: '100%', maxWidth: 'none', maxHeight: 'none',
          margin: 0, padding: 0, border: 'none', display: 'flex', flexDirection: 'column', color: 'var(--text-color)',
          backgroundColor: 'var(--surface)', boxShadow: '-4px 0 24px rgba(0,0,0,0.18)',
        }}
      >
        <div style={{ padding: '1.25rem 1.5rem 0', flexShrink: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', marginBottom: '0.75rem' }}>
            <div style={{ fontWeight: 600, fontSize: '1.0625rem', wordBreak: 'break-word' }}>{project.name}</div>
            <button
              onClick={onClose}
              aria-label="Close"
              title="Close"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: '0.125rem', lineHeight: 0, color: 'var(--text-muted)' }}
            >
              <X size={20} aria-hidden />
            </button>
          </div>
          {/* Wraps rather than scrolls: an overflow box turned the tabs' -1px underline overlap
              into a vertical scrollbar with arrows. */}
          <div role="tablist" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem', borderBottom: '1px solid var(--border)' }}>
            {tabs.map(t => {
              const active = t === tab
              return (
                <button
                  key={t}
                  role="tab"
                  aria-selected={active}
                  onClick={() => choose(t)}
                  style={{
                    padding: '0.5rem 0.75rem', marginBottom: -1, border: 'none', background: 'transparent', cursor: 'pointer',
                    fontSize: '0.8125rem', fontWeight: active ? 600 : 500, whiteSpace: 'nowrap',
                    color: active ? 'var(--primary)' : 'var(--text-muted)',
                    borderBottom: `2px solid ${active ? 'var(--primary)' : 'transparent'}`,
                  }}
                >
                  {TAB_LABEL[t]}
                </button>
              )
            })}
          </div>
        </div>

        <div role="tabpanel" style={{ flex: 1, overflowY: 'auto', padding: '1.25rem 1.5rem' }}>
          {!tab && <p style={MUTED}>Loading…</p>}
          {tab === 'details' && (
            <>
              <ProjectDetailsPanel projectId={project.id} onSaved={onDetailsSaved} />
              <div style={{ borderTop: '1px solid var(--border)', marginTop: '1.5rem', paddingTop: '1rem' }}>
                <button
                  onClick={onDelete}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.35rem', padding: '0.4rem 0.75rem', fontSize: '0.8125rem',
                    border: '1px solid var(--error, #f44336)', borderRadius: 'var(--radius)', background: 'transparent',
                    color: 'var(--error, #f44336)', cursor: 'pointer',
                  }}
                >
                  <Trash2 size={14} aria-hidden />
                  Delete project
                </button>
              </div>
            </>
          )}
          {tab === 'members' && (
            <ProjectMembersPanel projectId={project.id} projectName={project.name} canManage={admin} />
          )}
          {tab === 'defaults' && (
            <ProjectDefaultsPanel projectId={project.id} canEdit={admin} onSaved={save.report} />
          )}
          {tab === 'notifications' && (
            <NotificationRulesPanel projectId={project.id} onSaved={save.report} />
          )}
        </div>

        {save.status && (
          <div style={{ flexShrink: 0, padding: '0.6rem 1.5rem', borderTop: '1px solid var(--border)' }}>
            <SaveStatus status={save.status} />
          </div>
        )}
      </dialog>
    </div>
  )
}
