import { useState, useEffect, useMemo } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { JobProgress } from '../common'

export function GenerateManifest() {
  const { user } = useAuth()

  const [jobId, setJobId] = useState<string | null>(null)

  // Project-based state
  const [selectedOrgId, setSelectedOrgId] = useState<string>('')
  const [selectedProjectId, setSelectedProjectId] = useState<string>('')
  const [githubBranch, setGithubBranch] = useState<string>('main')

  // Fetch GitHub branches
  const { data: branches } = useQuery({
    queryKey: ['manifestBranches'],
    queryFn: async () => {
      const res = await apiClient.get('/api/manifest/branches')
      return (res as any).data || ['main']
    },
  })

  // Fetch user's organisations
  const { data: userOrgs } = useQuery({
    queryKey: ['userOrgs', user?.id],
    queryFn: async () => {
      const { data: roles, error: rolesErr } = await supabase
        .from('user_roles')
        .select('scope_id')
        .eq('user_id', user!.id)
        .eq('scope_type', 'organisation')
        .eq('is_active', true)
        .is('deleted_at', null)
      if (rolesErr) throw rolesErr
      if (!roles?.length) return []

      const orgIds = [...new Set(roles.map(r => r.scope_id))]
      const { data: orgs, error: orgsErr } = await supabase
        .from('organisations')
        .select('id, name')
        .in('id', orgIds)
      if (orgsErr) throw orgsErr
      return (orgs || []).sort((a, b) => a.name.localeCompare(b.name))
    },
    enabled: !!user
  })

  // Fetch projects for selected org
  const { data: projects, isLoading: isLoadingProjects } = useQuery({
    queryKey: ['orgProjects', selectedOrgId],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('projects')
        .select('id, name, model_id, ai_models(id, name, version, model_family_id, version_number)')
        .eq('organisation_id', selectedOrgId)
        .eq('is_active', true)
        .is('deleted_at', null)
        .order('name')
      if (error) throw error
      return data || []
    },
    enabled: !!selectedOrgId
  })

  // Auto-select first org
  useEffect(() => {
    if (userOrgs?.length && !selectedOrgId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedOrgId(userOrgs[0].id)
    }
  }, [userOrgs, selectedOrgId])

  // Auto-select first project
  useEffect(() => {
    if (projects?.length && !selectedProjectId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedProjectId(projects[0].id)
    } else if (projects && !projects.length) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedProjectId('')
    }
  }, [projects, selectedProjectId])

  // Resolve model info from selected project
  const projectModelInfo = useMemo(() => {
    if (!projects || !selectedProjectId) return null
    const project = projects.find((p: any) => p.id === selectedProjectId) as any
    if (!project) return null
    if (!project.model_id || !project.ai_models) return { hasModel: false }

    const model = project.ai_models
    const verNum = model.version_number

    if (!verNum) return { hasModel: true, incomplete: true, name: model.name }

    return {
      hasModel: true,
      incomplete: false,
      name: model.name,
      version: model.version,
      versionNumber: verNum,
      filename: `V${verNum}.TFL`, // Backend handles full filename resolution
    }
  }, [projects, selectedProjectId])

  const generateMutation = useMutation({
    mutationFn: () => {
      return apiClient.post('/api/manifest/generate', {
        model_source: 'My Project',
        model_name: projectModelInfo?.name || 'None',
        project_id: selectedProjectId,
        github_branch: githubBranch,
      })
    },
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
    },
  })

  const formIsValid = () => {
    if (!selectedProjectId) return false
    if (projectModelInfo?.incomplete) return false
    return true
  }

  const selectStyle = {
    width: '100%',
    padding: '0.5rem',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    backgroundColor: 'var(--surface)',
    color: 'var(--text-color)',
  }

  const labelStyle = {
    display: 'block' as const,
    fontSize: '0.8125rem',
    fontWeight: 500,
    marginBottom: '0.25rem',
  }

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Generate Firmware Manifest</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem', lineHeight: 1.5 }}>
        Generate a MANIFEST.zip with everything your device needs.
        Download it, unzip, copy the <code>MANIFEST</code> folder to an SD card, and insert it
        into your Wildlife Watcher. The mobile app will detect the files are already present
        and skip the slow BLE transfer.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1.5rem', maxWidth: '600px' }}>

        <div style={{ padding: '1rem', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

            {!user ? (
              <div style={{ padding: '0.5rem', color: 'var(--warning, #f59e0b)' }}>
                Please log in to access your projects.
              </div>
            ) : (
              <>
                {/* Branch selector */}
                <div>
                  <label style={labelStyle}>Firmware Branch</label>
                  <select
                    value={githubBranch}
                    onChange={(e) => setGithubBranch(e.target.value)}
                    style={selectStyle}
                  >
                    {(branches || ['main']).map((b: string) => (
                      <option key={b} value={b}>{b}</option>
                    ))}
                  </select>
                  <p style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>
                    Branch of the Grove Vision AI firmware repo for CONFIG.TXT, output.img, etc.
                  </p>
                </div>

                {/* Org selector */}
                {userOrgs && userOrgs.length > 1 && (
                  <div>
                    <label style={labelStyle}>Organisation</label>
                    <select
                      value={selectedOrgId}
                      onChange={(e) => { setSelectedOrgId(e.target.value); setSelectedProjectId('') }}
                      style={selectStyle}
                    >
                      {userOrgs.map((org: any) => (
                        <option key={org.id} value={org.id}>{org.name}</option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Project selector */}
                {selectedOrgId && (
                  <div>
                    <label style={labelStyle}>Project</label>
                    {isLoadingProjects ? (
                      <div style={{ padding: '0.5rem', opacity: 0.6 }}>Loading projects…</div>
                    ) : projects && projects.length > 0 ? (
                      <select
                        value={selectedProjectId}
                        onChange={(e) => setSelectedProjectId(e.target.value)}
                        style={selectStyle}
                      >
                        {projects.map((p: any) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                    ) : (
                      <div style={{ padding: '0.5rem', color: 'var(--error)' }}>
                        No projects found for this organisation.
                      </div>
                    )}
                  </div>
                )}

                {/* Model info (read-only) */}
                {projectModelInfo && (
                  <div style={{
                    borderTop: '1px solid var(--border)',
                    paddingTop: '1rem',
                    marginTop: '0.5rem',
                  }}>
                    {projectModelInfo.hasModel ? (
                      projectModelInfo.incomplete ? (
                        <div style={{ color: 'var(--warning, #f59e0b)' }}>
                          ⚠️ Model <strong>{projectModelInfo.name}</strong> is missing firmware IDs.
                          Please ensure it has a model family and version number assigned.
                        </div>
                      ) : (
                        <div>
                          <h4 style={{ marginBottom: '0.5rem' }}>✅ Model Info</h4>
                          <div style={{ fontSize: '0.8125rem', display: 'grid', gap: '0.25rem' }}>
                            <div><strong>Model:</strong> {projectModelInfo.name} v{projectModelInfo.version}</div>
                            <div><strong>Firmware ID (OP 14):</strong> <code>{projectModelInfo.firmwareModelId}</code></div>
                            <div><strong>Version (OP 15):</strong> <code>{projectModelInfo.versionNumber}</code></div>
                            <div><strong>Filename:</strong> <code>{projectModelInfo.filename}</code></div>
                          </div>
                        </div>
                      )
                    ) : (
                      <div style={{ fontSize: '0.8125rem', opacity: 0.8 }}>
                        ℹ️ No AI model assigned to this project. The MANIFEST will be generated
                        <strong> without model files</strong>. To assign a model, use the mobile app.
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginTop: '1.5rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn"
          disabled={generateMutation.isPending || !!jobId || !formIsValid()}
          onClick={() => generateMutation.mutate()}
          style={{ padding: '0.75rem 2rem' }}
        >
          {generateMutation.isPending ? 'Submitting…' : 'Generate Manifest'}
        </button>
      </div>

      {generateMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem' }}>
          {(generateMutation.error as Error).message}
        </p>
      )}

      <JobProgress jobId={jobId} />
    </div>
  )
}
