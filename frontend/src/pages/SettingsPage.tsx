// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SettingsPage: configuration, opened from the avatar menu. Projects (a row opens that project's
// settings in a slide-over, its deployment count goes to its deployments), the account (email and
// password) and
// integrations. Tools stay in Toolkit; monitoring stays in Field. What each role sees is in
// ProjectSettingsDrawer and the guide, /guides/project-settings.

import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useCreatableOrgs } from '../hooks/useCreatableOrgs'
import { supabase } from '../config/supabase'
import { DataTable, type Column } from '../components/ui/DataTable'
import { CreateProjectModal, type CreatedProject } from '../components/data/CreateProjectModal'
import { apiClient } from '../lib/apiClient'
import { fetchSettingsProjects, type SettingsProject } from '../lib/settingsProjects'
import { showUndoToast } from '../components/common/undoToastBus'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { PageHelp } from '../components/common/PageHelp'
import { ProjectSettingsDrawer } from '../components/settings/ProjectSettingsDrawer'
import { ChangePasswordForm } from '../components/settings/ChangePasswordForm'
import { IntegrationsSection } from '../components/settings/IntegrationsSection'
import { SettingsSection } from '../components/settings/SettingsSection'

const NO_PROJECTS: SettingsProject[] = []

const MUTED: React.CSSProperties = { color: 'var(--text-muted)' }

const COUNT_LINK: React.CSSProperties = { fontWeight: 500, color: 'var(--primary)' }

export function SettingsPage() {
  const { user, isDemo } = useAuth()
  const { clearAll, toggleProject, reloadProjects } = useProjectSelection()
  const [searchParams] = useSearchParams()

  const queryClient = useQueryClient()
  const projectsKey = ['settings', 'projects', user?.id]
  const projectsQuery = useQuery({
    queryKey: projectsKey,
    queryFn: () => fetchSettingsProjects(supabase),
    enabled: !!user,
  })
  const projects = projectsQuery.data ?? NO_PROJECTS
  const loading = projectsQuery.isPending
  const error = projectsQuery.error?.message ?? null
  // Optimistic edits to the list after a create or delete, before any re-read.
  const editProjects = (edit: (rows: SettingsProject[]) => SettingsProject[]) =>
    queryClient.setQueryData<SettingsProject[]>(projectsKey, rows => edit(rows ?? []))

  // New Project is for the organisation roles the projects INSERT policy accepts (04 #5). The
  // demo is a viewer, so it gets no write controls even where the database would allow one.
  const { orgs: creatableOrgs } = useCreatableOrgs()
  const canCreate = !isDemo && creatableOrgs.length > 0
  // Opens Create Project when arrived with ?create=true (zero-project empty state).
  const [createOpen, setCreateOpen] = useState(() => searchParams.get('create') === 'true')

  // The project whose settings are open in the slide-over.
  const [panel, setPanel] = useState<{ id: string; name: string } | null>(null)
  const openKeys = useMemo(() => new Set(panel ? [panel.id] : []), [panel])

  // Delete confirmation (type the project name to confirm), offered from the slide-over to admins.
  const [deleteTarget, setDeleteTarget] = useState<SettingsProject | null>(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const askDelete = (id: string) => {
    const row = projects.find(p => p.id === id)
    if (!row) return
    setDeleteTarget(row)
    setDeleteConfirmText('')
    setDeleteError(null)
  }

  const doDeleteProject = async () => {
    if (!deleteTarget) return
    const target = deleteTarget
    setDeleting(true)
    setDeleteError(null)
    try {
      const res = await apiClient.del(`/api/projects/${target.id}`) as { deleted_at?: string }
      const deletedAt = res?.deleted_at
      setDeleteTarget(null)
      setPanel(null)
      editProjects(rows => rows.filter(p => p.id !== target.id))
      reloadProjects()
      if (deletedAt) {
        showUndoToast({
          message: `Deleted project "${target.name}"`,
          onUndo: async () => {
            await apiClient.post(`/api/projects/${target.id}/restore`, { deleted_at: deletedAt })
            void queryClient.invalidateQueries({ queryKey: projectsKey })
            reloadProjects()
          },
        })
      }
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Delete failed. You may not have permission.')
    } finally {
      setDeleting(false)
    }
  }

  const projectColumns = useMemo<Column<SettingsProject>[]>(() => [
    { key: 'name', label: 'Name', cellStyle: { fontWeight: 500 } },
    {
      key: 'description', label: 'Description', cellStyle: MUTED,
      render: r => r.description ?? '',
      getValue: r => r.description ?? '',
    },
    {
      key: 'deployment_count', label: 'Deployments', cellStyle: { width: '100px' },
      getValue: r => String(r.deployment_count),
      render: r => (
        <Link
          to="/insights?tab=deployments"
          onClick={e => { e.stopPropagation(); clearAll(); toggleProject(r.id) }}
          style={COUNT_LINK}
          title="View this project's deployments"
        >
          {r.deployment_count}
        </Link>
      ),
    },
    {
      key: 'created_at', label: 'Created', cellStyle: { width: '110px', fontSize: '0.75rem' },
      render: r => new Date(r.created_at).toLocaleDateString(),
    },
  ], [clearAll, toggleProject])

  const newProjectButton = canCreate && (
    <button
      className="btn"
      onClick={() => setCreateOpen(true)}
      style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', padding: '0.375rem 0.75rem', fontSize: '0.8125rem' }}
    >
      <Plus size={14} aria-hidden /> New project
    </button>
  )

  return (
    <div style={{ maxWidth: 960 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', margin: '0 0 1.75rem 0' }}>
        <h2 style={{ margin: 0 }}>Settings</h2>
        <PageHelp label="About settings" guide="/guides/project-settings" guideLabel="Read the settings guide">
          Your projects, your account and connected services. Click a project to see its details,
          members, defaults and notifications.
        </PageHelp>
      </div>

      {error && <p role="alert" style={{ color: 'var(--error)', marginBottom: '1rem' }}>{error}</p>}

      <SettingsSection title="Projects">
        {loading ? (
          <p style={MUTED}>Loading projects…</p>
        ) : (
          <DataTable<SettingsProject>
            columns={projectColumns}
            rows={projects}
            rowKey={r => r.id}
            searchable={projects.length > 10}
            searchPlaceholder="Search projects…"
            emptyMessage={canCreate ? 'No projects yet. Create one here or in the mobile app.' : 'No projects yet.'}
            onRowClick={r => setPanel({ id: r.id, name: r.name })}
            selectedKeys={openKeys}
            toolbar={newProjectButton}
            pageSize={50}
          />
        )}
      </SettingsSection>

      <SettingsSection title="Account">
        <div style={{ fontSize: '0.875rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div><span style={MUTED}>Email:</span> <strong>{user?.email}</strong></div>
          {!isDemo && <ChangePasswordForm />}
        </div>
      </SettingsSection>

      <IntegrationsSection />

      <CreateProjectModal
        open={createOpen && canCreate}
        onClose={() => setCreateOpen(false)}
        onCreated={(p: CreatedProject) => {
          editProjects(rows => [{
            id: p.id, name: p.name, description: null,
            created_at: new Date().toISOString(), deployment_count: 0,
          }, ...rows])
          // The top-bar and upload pickers read the shared list, not this page's (#299).
          reloadProjects()
          setCreateOpen(false)
        }}
      />

      {panel && (
        <ProjectSettingsDrawer
          key={panel.id}
          project={panel}
          onClose={() => setPanel(null)}
          onDetailsSaved={saved => {
            editProjects(rows => rows.map(p => (p.id === saved.id ? { ...p, name: saved.name, description: saved.description } : p)))
            setPanel(prev => (prev && prev.id === saved.id ? { ...prev, name: saved.name } : prev))
            // The top-bar and upload pickers read the shared list, not this page's (#299).
            reloadProjects()
          }}
          onDelete={() => askDelete(panel.id)}
        />
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete project"
        confirmLabel="Delete project"
        busyLabel="Deleting…"
        busy={deleting}
        confirmDisabled={deleteConfirmText !== deleteTarget?.name}
        error={deleteError}
        onConfirm={doDeleteProject}
        onCancel={() => setDeleteTarget(null)}
      >
        {deleteTarget && (
          <>
            <p style={{ margin: 0 }}>
              This deletes <strong>{deleteTarget.name}</strong> and all its data:
              {' '}<strong>{deleteTarget.deployment_count}</strong> deployment{deleteTarget.deployment_count !== 1 ? 's' : ''} plus
              their photos and detections. You can undo it right afterwards.
            </p>
            <label style={{ ...MUTED, fontSize: '0.78rem' }}>
              Type the project name to confirm:
              <input
                value={deleteConfirmText}
                onChange={e => setDeleteConfirmText(e.target.value)}
                placeholder={deleteTarget.name}
                style={{ width: '100%', marginTop: '0.3rem', padding: '0.45rem 0.55rem', fontSize: '0.85rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)', boxSizing: 'border-box' }}
              />
            </label>
          </>
        )}
      </ConfirmDialog>
    </div>
  )
}
