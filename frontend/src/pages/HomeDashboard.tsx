// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The signed-in home page (lazy, like every signed-in page; HomePage keeps the public one).
// Projects come in the header picker's order, the selected ones first; past eight they are
// compact rows (UI audit 01 C1). The getting-started steps follow the user's role and can be
// hidden.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Folder, FolderPlus, X } from 'lucide-react'
import { useAuth } from '../hooks/useAuth'
import { useProjectSelection } from '../hooks/useProjectSelection'
import { useWritableProjects } from '../hooks/useWritableProjects'
import { useCanCreateProject } from '../hooks/useCanCreateProject'
import { useFeatureEnabled } from '../hooks/useFeatureEnabled'
import { supabase } from '../config/supabase'
import { ThreeStepGuide } from '../components/common/ThreeStepGuide'
import { DEFAULT_SIGNED_IN_STEPS, VIEWER_STEPS } from '../components/common/guideSteps'
import { APP_STORE_URL } from '../components/common/StoreBadges'
import { usePageMeta } from '../lib/pageMeta'
import { selectedFirst } from '../lib/projectPicker'

interface HomeProject {
  id: string
  name: string
  description: string | null
  deployment_count: number
}

/** Past this many projects the rest are compact rows, not cards. */
const CARD_LIMIT = 8

// A per-viewer convenience: the steps stay hidden in this browser once dismissed.
const GUIDE_HIDDEN_KEY = 'ww:homeGuideHidden'

function readGuideHidden(): boolean {
  try { return localStorage.getItem(GUIDE_HIDDEN_KEY) === '1' } catch { return false }
}

function writeGuideHidden(hidden: boolean) {
  try {
    if (hidden) localStorage.setItem(GUIDE_HIDDEN_KEY, '1')
    else localStorage.removeItem(GUIDE_HIDDEN_KEY)
  } catch { /* private mode */ }
}

/** The first name from the sign-up form or the Google profile, never the email address. */
function givenName(meta: Record<string, unknown> | undefined): string | null {
  const value = meta?.given_name ?? meta?.full_name ?? meta?.name
  return typeof value === 'string' && value.trim() ? value.trim().split(/\s+/)[0] : null
}

const deploymentsLabel = (n: number) => `${n} ${n === 1 ? 'deployment' : 'deployments'}`

const TEXT_BUTTON: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '0.25rem', padding: 0, border: 'none',
  background: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '0.8125rem',
}

const HEALTH_LINK: React.CSSProperties = { fontSize: '0.75rem', color: 'var(--primary)', whiteSpace: 'nowrap' }

function ProjectCardTile({ project, showHealth, onOpen }: { project: HomeProject; showHealth: boolean; onOpen: () => void }) {
  return (
    <div
      className="card"
      onClick={onOpen}
      style={{
        padding: '1.25rem', cursor: 'pointer', minWidth: 0,
        display: 'flex', flexDirection: 'column', gap: '0.75rem',
        transition: 'transform 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-3px)'; e.currentTarget.style.boxShadow = '0 8px 24px rgba(76,175,80,0.12)' }}
      onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = 'none' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
        <div style={{
          width: 40, height: 40, borderRadius: 10, flexShrink: 0, color: 'var(--primary)',
          background: 'linear-gradient(135deg,rgba(76,175,80,0.3),rgba(76,175,80,0.08))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}><Folder size={20} aria-hidden="true" /></div>
        <div style={{ minWidth: 0 }}>
          <div title={project.name} style={{ fontWeight: 600, fontSize: '0.9375rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {project.name}
          </div>
          {project.description && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {project.description}
            </div>
          )}
        </div>
      </div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{deploymentsLabel(project.deployment_count)}</div>
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: 'auto' }} onClick={e => e.stopPropagation()}>
        <button onClick={onOpen} className="btn" style={{ flex: 1, fontSize: '0.75rem', padding: '0.375rem 0' }}>
          Open
        </button>
        {showHealth && <Link to={`/intelligence/${project.id}`} style={HEALTH_LINK}>Dataset health</Link>}
      </div>
    </div>
  )
}

function ProjectRow({ project, first, showHealth, onOpen }: { project: HomeProject; first: boolean; showHealth: boolean; onOpen: () => void }) {
  return (
    <li style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.5rem 0.875rem', borderTop: first ? 'none' : '1px solid var(--border)', fontSize: '0.8125rem' }}>
      <button
        onClick={onOpen}
        title={project.name}
        style={{ flex: 1, minWidth: 0, padding: 0, border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left', color: 'var(--text-color)', fontWeight: 600, fontSize: 'inherit', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
      >
        {project.name}
      </button>
      <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{deploymentsLabel(project.deployment_count)}</span>
      {showHealth && <Link to={`/intelligence/${project.id}`} style={HEALTH_LINK}>Dataset health</Link>}
    </li>
  )
}

export function HomeDashboard() {
  usePageMeta({ title: 'Home' })
  const { user } = useAuth()
  const navigate = useNavigate()
  // `projects` changes when the shared list reloads (e.g. an accepted invitation), which
  // refetches the cards below.
  const { clearAll, toggleProject, projects: selectableProjects, selectedProjectIds } = useProjectSelection()
  const { canWriteAny, loading: rolesLoading } = useWritableProjects()
  const [guideHidden, setGuideHidden] = useState(readGuideHidden)

  // One read: each project with its live deployment count, not its deployment rows (01 C3).
  const { data: projects, isLoading } = useQuery({
    queryKey: ['homeProjects', user?.id, selectableProjects],
    enabled: !!user,
    queryFn: async (): Promise<HomeProject[]> => {
      const { data, error } = await supabase
        .from('projects')
        .select('id, name, description, deployments(count)')
        .is('deleted_at', null)
        .is('deployments.deleted_at', null)
        .order('name')
      if (error) throw error
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (data ?? []).map((p: any) => ({
        id: p.id, name: p.name, description: p.description,
        deployment_count: Array.isArray(p.deployments) ? (p.deployments[0]?.count ?? 0) : 0,
      }))
    },
  })
  const ordered = selectedFirst(projects ?? [], selectedProjectIds)
  const canCreate = useCanCreateProject(!isLoading && ordered.length === 0)
  // Dataset Health is behind FF_INTELLIGENCE_ENABLED (decision 6): one cached probe, not one per card.
  const showHealth = useFeatureEnabled('intelligence', ordered[0]?.id)

  const open = (id: string) => { clearAll(); toggleProject(id); navigate('/insights?tab=deployments') }
  const hideGuide = (hidden: boolean) => { writeGuideHidden(hidden); setGuideHidden(hidden) }
  const name = givenName(user?.user_metadata)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <h2 style={{ margin: 0, fontSize: '1.75rem' }}>
        {name ? `Welcome back, ${name}` : 'Welcome back'}
      </h2>

      {/* Getting started: upload, review, insights; browse, insights, export for a viewer */}
      {!rolesLoading && !guideHidden && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '1rem' }}>
            <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: '0.9375rem' }}>
              {canWriteAny ? 'Three steps from SD card to results.' : 'Three ways to explore your projects.'}
            </p>
            <button onClick={() => hideGuide(true)} style={TEXT_BUTTON} aria-label="Hide the getting started steps">
              <X size={14} aria-hidden="true" /> Hide
            </button>
          </div>
          <ThreeStepGuide steps={canWriteAny ? DEFAULT_SIGNED_IN_STEPS : VIEWER_STEPS} />
        </section>
      )}

      {!isLoading && ordered.length > 0 && (
        <section>
          <h3 style={{ margin: '0 0 1rem 0', fontSize: '1.125rem', fontWeight: 600 }}>
            Your projects ({ordered.length})
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '1rem' }}>
            {ordered.slice(0, CARD_LIMIT).map(p => (
              <ProjectCardTile key={p.id} project={p} showHealth={showHealth} onOpen={() => open(p.id)} />
            ))}
          </div>
          {ordered.length > CARD_LIMIT && (
            <ul className="card" style={{ listStyle: 'none', margin: '1rem 0 0', padding: 0, overflow: 'hidden' }}>
              {ordered.slice(CARD_LIMIT).map((p, i) => (
                <ProjectRow key={p.id} project={p} first={i === 0} showHealth={showHealth} onOpen={() => open(p.id)} />
              ))}
            </ul>
          )}
        </section>
      )}

      {/* A user with no project: create one where the role allows (01 B2) */}
      {!isLoading && ordered.length === 0 && (
        <div className="card" style={{ padding: '2.5rem', textAlign: 'center' }}>
          <FolderPlus size={36} color="var(--primary)" aria-hidden="true" style={{ marginBottom: '0.75rem' }} />
          <h3 style={{ marginTop: 0 }}>No projects yet</h3>
          {canCreate ? (
            <>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', maxWidth: '400px', margin: '0 auto 1.5rem' }}>
                Create your first project to start adding deployments and photos. You can also create
                one in the{' '}
                <a href={APP_STORE_URL} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>Wildlife Watcher mobile app</a>.
              </p>
              {/* ?create=true makes SettingsPage open the CreateProjectModal */}
              <Link to="/settings?create=true" className="btn" style={{ textDecoration: 'none' }}>
                Create a project
              </Link>
            </>
          ) : (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', maxWidth: '400px', margin: '0 auto' }}>
              Ask a project admin to invite you. The invitation shows at the top of this page.
            </p>
          )}
        </div>
      )}

      {guideHidden && (
        <button onClick={() => hideGuide(false)} style={{ ...TEXT_BUTTON, alignSelf: 'flex-start' }}>
          Show the getting started steps
        </button>
      )}
    </div>
  )
}
