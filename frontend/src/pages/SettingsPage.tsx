// Copyright (c) 2024
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SettingsPage — configuration surface (opened from the avatar menu). Holds ONLY
// configuration: account, projects & members (moved here from Results in P2), and —
// later — capture defaults, default model, and notification rules (P5–P6).
// Tools stay in Toolkit; monitoring stays in Field.
 
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { supabase } from '../config/supabase'
import { DataTable, type Column } from '../components/ui/DataTable'
import { Modal } from '../components/ui/Modal'
import { NAV_BTN } from '../components/data/DeploymentActionRow'
import { CreateProjectModal, type CreatedProject } from '../components/data/CreateProjectModal'
import { DemoDisabled, useDemoGuard } from '../components/common/DemoGuard'
import { apiClient } from '../lib/apiClient'
import { showUndoToast } from '../components/common/undoToastBus'
import { ProjectMembersPanel } from '../components/data/ProjectMembersPanel'
import { NotificationRulesPanel } from '../components/settings/NotificationRulesPanel'
import { ProjectDefaultsPanel } from '../components/settings/ProjectDefaultsPanel'
import { ProjectDetailsPanel } from '../components/settings/ProjectDetailsPanel'
import { InaturalistPanel } from '../components/settings/InaturalistPanel'
import { adminScope, isProjectAdmin, type AdminScope, type OwnRole } from '../lib/moveDeployment'

interface ProjectRow {
  id: string
  name: string
  description: string | null
  created_at: string
  deployment_count: number
  organisation_id: string
}

const NO_PROJECTS: ProjectRow[] = []

async function fetchProjectRows(): Promise<ProjectRow[]> {
  const { data, error } = await supabase
    .from('projects')
    .select('id, name, description, organisation_id, created_at, deployments(id)')
    .is('deleted_at', null)
    .is('deployments.deleted_at', null)
    .order('created_at', { ascending: false })
  if (error) throw new Error(error.message)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (data || []).map((p: any) => ({
    id: p.id,
    name: p.name,
    description: p.description,
    organisation_id: p.organisation_id,
    created_at: p.created_at,
    deployment_count: Array.isArray(p.deployments) ? p.deployments.length : 0,
  }))
}

const PANEL_TITLE = {
  details: 'Project Details', members: 'Project Members', defaults: 'Project Defaults', notifications: 'Notifications',
} as const

function formatDate(s: string | null) {
  return s ? new Date(s).toLocaleDateString() : '—'
}

function Section({ title, description, children }: {
  title: string; description?: string; children: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: '2rem' }}>
      <h3 style={{ margin: '0 0 0.25rem 0', fontSize: '1.0625rem' }}>{title}</h3>
      {description && <p style={{ margin: '0 0 0.875rem 0', opacity: 0.65, fontSize: '0.85rem' }}>{description}</p>}
      {children}
    </section>
  )
}

export function SettingsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { clearAll, toggleProject, reloadProjects } = useProjectSelection()
  const { guard } = useDemoGuard()
  const [searchParams] = useSearchParams()

  const queryClient = useQueryClient()
  const projectsKey = ['settings', 'projects', user?.id]
  const projectsQuery = useQuery({
    queryKey: projectsKey,
    queryFn: fetchProjectRows,
    enabled: !!user,
  })
  const projects = projectsQuery.data ?? NO_PROJECTS
  const loading = projectsQuery.isPending
  const error = projectsQuery.error?.message ?? null
  // Optimistic edits to the list after a create or delete, before any re-read.
  const editProjects = (edit: (rows: ProjectRow[]) => ProjectRow[]) =>
    queryClient.setQueryData<ProjectRow[]>(projectsKey, rows => edit(rows ?? []))

  // Which projects the user may delete: the database's rule, project_admin of the project or ww_admin.
  const [adminProjectIds, setAdminProjectIds] = useState<Set<string>>(new Set())
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)
  const canDeleteProject = (id: string) => isSuperAdmin || adminProjectIds.has(id)
  // Who may edit a project's details: the projects UPDATE policy (project_admin or ww_admin).
  const [editScope, setEditScope] = useState<AdminScope>({ wwAdmin: false, adminProjectIds: new Set() })

  // Delete-confirmation modal (type the project name to confirm).
  const [deleteTarget, setDeleteTarget] = useState<ProjectRow | null>(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)

  // Auto-open Create Project when arrived with ?create=true (zero-project empty state)
  const [createOpen, setCreateOpen] = useState(() => searchParams.get('create') === 'true')
  // Per-project slide-over: details, members, capture/AI defaults, or notification rules.
  type PanelKind = 'details' | 'members' | 'defaults' | 'notifications'
  const [panel, setPanel] = useState<{ kind: PanelKind; id: string; name: string; org_id: string } | null>(null)

  // Load the user's roles to decide which projects show a Delete action.
  useEffect(() => {
    if (!user) return
    let cancelled = false
    supabase
      .from('user_roles')
      .select('scope_id, scope_type, role, is_active, expires_at')
      .eq('user_id', user.id)
      .then(({ data }) => {
        if (cancelled || !data) return
        const admins = new Set<string>()
        let sup = false
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        for (const r of data as any[]) {
          if (r.scope_type === 'system' && r.role === 'ww_admin') sup = true
          if (r.scope_type === 'project' && r.role === 'project_admin') admins.add(r.scope_id)
        }
        setAdminProjectIds(admins)
        setIsSuperAdmin(sup)
        setEditScope(adminScope(data as OwnRole[]))
      })
    return () => { cancelled = true }
  }, [user])

  const doDeleteProject = async () => {
    if (!deleteTarget) return
    const target = deleteTarget
    setDeleting(true)
    try {
      const res = await apiClient.del(`/api/projects/${target.id}`) as { deleted_at?: string }
      const deletedAt = res?.deleted_at
      setDeleteTarget(null)
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
      alert(e instanceof Error ? e.message : 'Delete failed — you may not have permission.')
    } finally {
      setDeleting(false)
    }
  }

  const projectColumns = useMemo<Column<ProjectRow>[]>(() => [
    { key: 'name', label: 'Name', cellStyle: { fontWeight: 500 } },
    {
      key: 'description', label: 'Description',
      render: r => r.description || <span style={{ opacity: 0.4 }}>—</span>,
      getValue: r => r.description ?? '',
    },
    {
      key: 'deployment_count', label: 'Deployments', cellStyle: { width: '100px' },
      getValue: r => String(r.deployment_count), render: r => r.deployment_count,
    },
    {
      key: 'created_at', label: 'Created', cellStyle: { width: '110px', fontSize: '0.75rem' },
      render: r => formatDate(r.created_at),
    },
    {
      key: '_actions', label: 'Actions', sortable: false, hideable: false, cellStyle: { width: '320px' },
      render: r => (
        <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap' }}>
          <button
            style={NAV_BTN}
            onClick={e => { e.stopPropagation(); clearAll(); toggleProject(r.id); navigate('/insights?tab=deployments') }}
            title="View this project's deployments"
          >
            📍 Open
          </button>
          <button
            style={NAV_BTN}
            onClick={e => { e.stopPropagation(); navigate(`/intelligence/${r.id}`) }}
            title="Dataset health dashboard"
          >
            📊 Health
          </button>
          {isProjectAdmin(editScope, r.id) && (
            <button
              style={{ ...NAV_BTN, color: 'var(--text-color)' }}
              onClick={e => { e.stopPropagation(); setPanel({ kind: 'details', id: r.id, name: r.name, org_id: r.organisation_id }) }}
              title="Name, description and website (Project Admin)"
            >
              ✎ Details
            </button>
          )}
          <button
            style={{ ...NAV_BTN, color: 'var(--text-color)' }}
            onClick={e => { e.stopPropagation(); setPanel({ kind: 'members', id: r.id, name: r.name, org_id: r.organisation_id }) }}
            title="Manage project members"
          >
            👥 Members
          </button>
          <button
            style={{ ...NAV_BTN, color: 'var(--text-color)' }}
            onClick={e => { e.stopPropagation(); setPanel({ kind: 'defaults', id: r.id, name: r.name, org_id: r.organisation_id }) }}
            title="Default triggering method, AI model, photos per trigger, detection threshold and capture flash (Project Admin)"
          >
            ⚙ Defaults
          </button>
          <button
            style={{ ...NAV_BTN, color: 'var(--text-color)' }}
            onClick={e => { e.stopPropagation(); setPanel({ kind: 'notifications', id: r.id, name: r.name, org_id: r.organisation_id }) }}
            title="Your alert preferences for this project"
          >
            🔔 Notifications
          </button>
          {canDeleteProject(r.id) && (
            <button
              style={{ ...NAV_BTN, color: 'var(--error, #f44336)' }}
              onClick={e => { e.stopPropagation(); guard(() => { setDeleteTarget(r); setDeleteConfirmText('') })() }}
              title="Delete this project and all its data"
            >
              🗑 Delete
            </button>
          )}
        </div>
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [clearAll, toggleProject, navigate, guard, adminProjectIds, isSuperAdmin, editScope])

  return (
    <div style={{ maxWidth: 960 }}>
      <h2 style={{ margin: '0 0 0.375rem 0' }}>⚙ Settings</h2>
      <p style={{ opacity: 0.65, fontSize: '0.9rem', margin: '0 0 1.75rem 0' }}>
        Manage your account and projects. Use the per-project actions to manage members, set
        capture &amp; AI defaults, and choose your notifications.
      </p>

      {error && <p style={{ color: 'var(--error)', marginBottom: '1rem' }}>⚠ {error}</p>}

      <Section
        title="Projects"
        description="Each project's actions let you open it, view dataset health, edit its details, manage members, set capture & AI defaults, and configure your notifications."
      >
        <div style={{ marginBottom: '0.75rem' }}>
          <DemoDisabled tip="Creating projects is disabled in the demo">
            <button className="btn" onClick={() => setCreateOpen(true)} title="Create a new project">
              + New Project
            </button>
          </DemoDisabled>
        </div>
        {loading ? (
          <p style={{ opacity: 0.5 }}>Loading projects…</p>
        ) : (
          <DataTable<ProjectRow>
            columns={projectColumns}
            rows={projects}
            rowKey={r => r.id}
            searchable
            searchPlaceholder="Search projects…"
            exportFilename="projects"
            emptyMessage="No projects yet. Create one here, or in the Wildlife Watcher mobile app."
            onRowClick={r => { clearAll(); toggleProject(r.id); navigate('/insights?tab=deployments') }}
            pageSize={50}
          />
        )}
      </Section>

      <Section title="Account" description="Your sign-in details.">
        <div style={{ fontSize: '0.875rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div><span style={{ opacity: 0.6 }}>Email:</span> <strong>{user?.email ?? '—'}</strong></div>
          <div>
            <DemoDisabled tip="The demo account password can't be changed">
              <Link to="/reset-password" className="btn" style={{ textDecoration: 'none', width: 'fit-content', display: 'inline-block' }}>
                Change password →
              </Link>
            </DemoDisabled>
          </div>
        </div>
      </Section>

      <Section title="Integrations" description="External services linked to your account.">
        <InaturalistPanel />
      </Section>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(p: CreatedProject) => {
          editProjects(rows => [{
            id: p.id, name: p.name, description: null, organisation_id: '',
            created_at: new Date().toISOString(), deployment_count: 0,
          }, ...rows])
          // The top-bar and upload pickers read the shared list, not this page's (#299).
          reloadProjects()
          setCreateOpen(false)
        }}
      />

      {panel && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 200, display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end' }}>
          <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.35)' }} onClick={() => setPanel(null)} />
          <div style={{
            position: 'relative', zIndex: 1, width: 'min(560px, 100vw)', height: '100%',
            backgroundColor: 'var(--surface)', boxShadow: '-4px 0 24px rgba(0,0,0,0.18)', overflowY: 'auto', padding: '1.5rem',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '1.0625rem' }}>
                  {PANEL_TITLE[panel.kind]}
                </div>
                <div style={{ fontSize: '0.8rem', opacity: 0.6 }}>{panel.name}</div>
              </div>
              <button
                onClick={() => setPanel(null)}
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: '1.25rem', lineHeight: 1, color: 'var(--text-color)', opacity: 0.6 }}
                title="Close"
              >✕</button>
            </div>
            {panel.kind === 'details' && (
              <ProjectDetailsPanel
                projectId={panel.id}
                onSaved={saved => {
                  editProjects(rows => rows.map(p => (p.id === saved.id ? { ...p, name: saved.name, description: saved.description } : p)))
                  setPanel(prev => (prev && prev.id === saved.id ? { ...prev, name: saved.name } : prev))
                  // The top-bar and upload pickers read the shared list, not this page's (#299).
                  reloadProjects()
                }}
              />
            )}
            {panel.kind === 'members' && (
              <ProjectMembersPanel projectId={panel.id} projectName={panel.name} />
            )}
            {panel.kind === 'defaults' && (
              <>
                <p style={{ fontSize: '0.8rem', opacity: 0.65, margin: '0 0 0.875rem 0' }}>
                  Default triggering method, AI model, photos per trigger, detection threshold and capture flash for this project's deployments. Cameras pick up changes at their next deployment. Requires the Project Admin role.
                </p>
                <ProjectDefaultsPanel projectId={panel.id} />
              </>
            )}
            {panel.kind === 'notifications' && (
              <>
                <p style={{ fontSize: '0.8rem', opacity: 0.65, margin: '0 0 0.875rem 0' }}>
                  Choose which alerts you receive for this project, and how. You're only notified for
                  what you select here. In-app works now; email activates once a provider is configured;
                  mobile push is delivered by the app.
                </p>
                <NotificationRulesPanel projectId={panel.id} />
              </>
            )}
          </div>
        </div>
      )}

      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete project" size="sm">
        {deleteTarget && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <p style={{ fontSize: '0.85rem', margin: 0 }}>
              This deletes <strong>{deleteTarget.name}</strong> and all its data —
              {' '}<strong>{deleteTarget.deployment_count}</strong> deployment{deleteTarget.deployment_count !== 1 ? 's' : ''} plus
              their photos and detections. It's a soft delete, so you can undo it right afterwards.
            </p>
            <label style={{ fontSize: '0.78rem', opacity: 0.75 }}>
              Type the project name to confirm:
              <input
                value={deleteConfirmText}
                onChange={e => setDeleteConfirmText(e.target.value)}
                placeholder={deleteTarget.name}
                style={{ width: '100%', marginTop: '0.3rem', padding: '0.45rem 0.55rem', fontSize: '0.85rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface)', color: 'var(--text-color)' }}
              />
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button onClick={() => setDeleteTarget(null)} style={{ ...NAV_BTN }}>Cancel</button>
              <button
                onClick={doDeleteProject}
                disabled={deleteConfirmText !== deleteTarget.name || deleting}
                style={{
                  padding: '0.4rem 0.9rem', fontSize: '0.82rem', fontWeight: 600, border: 'none', borderRadius: 'var(--radius)',
                  background: 'var(--error, #f44336)', color: '#fff',
                  cursor: deleteConfirmText !== deleteTarget.name || deleting ? 'not-allowed' : 'pointer',
                  opacity: deleteConfirmText !== deleteTarget.name || deleting ? 0.5 : 1,
                }}
              >
                {deleting ? 'Deleting…' : 'Delete project'}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
