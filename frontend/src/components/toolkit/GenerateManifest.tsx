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

  // Fetch accessible projects
  const { data: projects, isLoading: isLoadingProjects } = useQuery({
    queryKey: ['allProjects', user?.id],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('projects')
        .select('id, name, model_id, ai_models(id, name, version, model_family_id, version_number, ai_model_families(firmware_model_id)), organisations(name)')
        .eq('is_active', true)
        .is('deleted_at', null)
        .order('name')
      if (error) throw error
      return data || []
    },
    enabled: !!user
  })

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
    const family = model.ai_model_families
    const fwId = family?.firmware_model_id
    const verNum = model.version_number

    if (!fwId || !verNum) return { hasModel: true, incomplete: true, name: model.name }

    return {
      hasModel: true,
      incomplete: false,
      name: model.name,
      version: model.version,
      firmwareModelId: fwId,
      versionNumber: verNum,
      filename: `${fwId}V${verNum}.TFL`,
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
      <h3 style={{ marginBottom: '0.5rem' }}>Prepare SD Card</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem', lineHeight: 1.5 }}>
        While the mobile app can update your camera wirelessly, AI Models and System Software are large files that take a long time to send over Bluetooth.
        <br/><br/>
        For the fastest setup, download this package to your computer and move it to your SD card. When you insert the card into your Wildlife Watcher, it will instantly recognize the files and skip the long wireless wait.
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
                  <label style={labelStyle}>Software Version</label>
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
                    Select the system version for your camera hardware.
                  </p>
                </div>

                {/* Project selector */}
                <div>
                  <label style={labelStyle}>Project & Species AI</label>
                  {isLoadingProjects ? (
                    <div style={{ padding: '0.5rem', opacity: 0.6 }}>Loading projects…</div>
                  ) : projects && projects.length > 0 ? (
                    <select
                      value={selectedProjectId}
                      onChange={(e) => setSelectedProjectId(e.target.value)}
                      style={selectStyle}
                    >
                      {projects.map((p: any) => (
                        <option key={p.id} value={p.id}>
                          {p.name} {p.organisations?.name ? `(${p.organisations.name})` : ''}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div style={{ padding: '0.5rem', color: 'var(--error)' }}>
                      No accessible projects found.
                    </div>
                  )}
                  <p style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>
                    Choose which project this camera belongs to. This includes the specific AI "brain" for identifying animals.
                  </p>
                </div>

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
                      <div style={{ fontSize: '0.8125rem', opacity: 0.8, color: 'var(--primary)' }}>
                        🤖 <strong>Note on AI Identification:</strong>
                        <br/>
                        This project doesn't have an AI model assigned yet. The setup folder will include the camera system and settings, but it won't be able to identify species automatically. You can add a "Species Brain" later via the Project Settings in the app.
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
          {generateMutation.isPending ? 'Submitting…' : '📥 Download Setup Folder'}
        </button>
      </div>

      {generateMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem' }}>
          {(generateMutation.error as Error).message}
        </p>
      )}

      <JobProgress jobId={jobId} />

      {jobId && (
        <div style={{
          marginTop: '2rem',
          padding: '1.5rem',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--primary)',
          borderRadius: 'var(--radius)',
        }}>
          <h4 style={{ marginTop: 0, marginBottom: '1rem', color: 'var(--primary)' }}>Next Steps Checklist:</h4>
          <ul style={{ listStyleType: 'none', paddingLeft: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <li><label style={{ cursor: 'pointer' }}><input type="checkbox" style={{ marginRight: '0.5rem' }} /> Unzip the downloaded folder.</label></li>
            <li><label style={{ cursor: 'pointer' }}><input type="checkbox" style={{ marginRight: '0.5rem' }} /> Copy the "MANIFEST" folder with its contents as it is to the root of your SD card.</label></li>
            <li><label style={{ cursor: 'pointer' }}><input type="checkbox" style={{ marginRight: '0.5rem' }} /> Insert the card in your Wildlife Watcher and power on the device.</label></li>
            <li><label style={{ cursor: 'pointer' }}><input type="checkbox" style={{ marginRight: '0.5rem' }} /> Connect the device with your app and start monitoring.</label></li>
          </ul>
        </div>
      )}
    </div>
  )
}
