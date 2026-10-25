import { useState, useEffect, useMemo } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, CircleCheck, Download, LoaderCircle, Package, RefreshCw } from 'lucide-react'
import { apiClient } from '../../lib/apiClient'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { useJob } from '../../hooks/useJob'
import { useProjectSelection } from '../../hooks/useProjectSelection'
import { useWritableProjects } from '../../hooks/useWritableProjects'
import { PICKER_SEARCH_MIN, filterProjects } from '../../lib/projectPicker'
import { toSelectableProject } from '../../lib/projectSelection'
import { ErrorBanner } from '../common'
import { PageHelp } from '../common/PageHelp'

type Step = 'configure' | 'generating' | 'ready'

/** The guide that holds the background: why an SD card, the two camera variants, what to do after. */
const SD_CARD_GUIDE = '/guides/preparing-an-sd-card'

export function GenerateManifest() {
  const { user } = useAuth()
  const { selectedProjectIds } = useProjectSelection()
  const { writable } = useWritableProjects()

  const [step, setStep] = useState<Step>('configure')
  const [jobId, setJobId] = useState<string | null>(null)

  // Project-based state: the user's choice, else the default derived below.
  const [chosenProjectId, setChosenProjectId] = useState<string>('')
  const [projectQuery, setProjectQuery] = useState('')
  const [selectedFirmwareId, setSelectedFirmwareId] = useState<string>('')

  // Fetch Himax firmware versions from DB
  const { data: firmwares, isLoading: isLoadingFirmwares } = useQuery({
    queryKey: ['himaxFirmwares'],
    queryFn: async () => {
      const { data, error } = await supabase
        .from('firmware')
        .select('id, name, version, is_active, created_at, camera_variant')
        .eq('type', 'himax')
        .is('deleted_at', null)
        .order('created_at', { ascending: false })
      if (error) throw error
      return data || []
    }
  })

  // Derive active/fallback firmware ID if none is explicitly selected
  const himaxFirmwareId = useMemo(() => {
    if (selectedFirmwareId) return selectedFirmwareId
    if (!firmwares?.length) return ''
    const active = firmwares.find((f: any) => f.is_active)
    return active ? active.id : firmwares[0].id
  }, [firmwares, selectedFirmwareId])

  const selectedFirmware = useMemo(() => {
    if (!firmwares || !himaxFirmwareId) return null
    return firmwares.find((f: any) => f.id === himaxFirmwareId)
  }, [firmwares, himaxFirmwareId])

  // Fetch accessible projects with their AI model
  const { data: allProjects, isLoading: isLoadingProjects } = useQuery({
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

  // Only the projects the user can write to: a camera is set up for a project they work in.
  const projects = useMemo(() => {
    const ids = new Set(writable.map(p => p.id))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (allProjects ?? []).flatMap((p: any) => (ids.has(p.id) ? [{ ...p, ...toSelectableProject(p) }] : []))
  }, [allProjects, writable])

  // Default to the single project ticked in the header, else the first one (UI audit 01 D2).
  const selectedProjectId = useMemo(() => {
    if (chosenProjectId && projects.some(p => p.id === chosenProjectId)) return chosenProjectId
    const headerPick = selectedProjectIds.length === 1 ? selectedProjectIds[0] : null
    if (headerPick && projects.some(p => p.id === headerPick)) return headerPick
    return projects[0]?.id ?? ''
  }, [chosenProjectId, projects, selectedProjectIds])

  // Above ten projects the select gets a search box; the chosen project always stays listed.
  const searchable = projects.length > PICKER_SEARCH_MIN
  const projectOptions = useMemo(() => {
    const matches = filterProjects(projects, searchable ? projectQuery : '')
    const chosen = projects.find(p => p.id === selectedProjectId)
    return chosen && !matches.includes(chosen) ? [chosen, ...matches] : matches
  }, [projects, searchable, projectQuery, selectedProjectId])

  // Resolve model info from selected project
  const projectModelInfo = useMemo(() => {
    if (!selectedProjectId) return null
    const project = projects.find(p => p.id === selectedProjectId)
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
    }
  }, [projects, selectedProjectId])

  // Poll job status
  const { data: job } = useJob(jobId)

  // Auto-transition to 'ready' when job completes
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (job && (job.status === 'completed' || job.status === 'completed_with_errors') && step === 'generating') {
      setStep('ready')
    }
  }, [job, step])
  /* eslint-enable react-hooks/set-state-in-effect */

  const generateMutation = useMutation({
    mutationFn: () => {
      return apiClient.post('/api/manifest/generate', {
        model_source: 'My Project',
        model_name: projectModelInfo?.name || 'None',
        project_id: selectedProjectId,
        github_branch: 'main',
        himax_firmware_id: himaxFirmwareId,
      })
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
      setStep('generating')
    },
  })

  const formIsValid = () => {
    if (!selectedProjectId) return false
    if (projectModelInfo?.incomplete) return false
    return true
  }

  const handleReset = () => {
    setJobId(null)
    setStep('configure')
    generateMutation.reset()
    setSelectedFirmwareId('')
  }

  // Get the selected project name for the summary
  const selectedProject = projects.find(p => p.id === selectedProjectId)

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
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', marginBottom: '1.5rem' }}>
        <h2 style={{ margin: 0 }}>Prepare SD card</h2>
        <PageHelp label="About preparing an SD card" guide={SD_CARD_GUIDE} guideLabel="Read the SD card guide">
          A package of camera software and your project&apos;s AI model to copy to an SD card, faster
          than sending it over Bluetooth.
        </PageHelp>
      </div>

      {/* ── Step indicator ──────────────────────────────────── */}
      <div style={{
        display: 'flex',
        gap: '0.5rem',
        marginBottom: '1.5rem',
        fontSize: '0.8125rem',
      }}>
        {(['configure', 'generating', 'ready'] as Step[]).map((s, i) => {
          const labels = ['1. Configure', '2. Generating', '3. Ready']
          const isActive = s === step
          const isDone = (step === 'generating' && i === 0) || (step === 'ready' && i < 2)
          return (
            <div
              key={s}
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '0.3rem',
                padding: '0.5rem 0.75rem',
                borderRadius: 'var(--radius)',
                fontWeight: isActive ? 600 : 400,
                backgroundColor: isActive ? 'var(--primary)' : isDone ? 'rgba(76,175,80,0.12)' : 'var(--surface)',
                color: isActive ? '#fff' : isDone ? 'var(--success)' : 'var(--text-muted)',
                border: `1px solid ${isActive ? 'var(--primary)' : 'var(--border)'}`,
                transition: 'background-color 0.2s ease, color 0.2s ease',
              }}
            >
              {isDone && <Check size={14} aria-hidden="true" />}{labels[i]}
            </div>
          )
        })}
      </div>

      {/* ── Step 1: Configure ──────────────────────────────── */}
      {step === 'configure' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1.5rem', maxWidth: '600px' }}>
          <div style={{ padding: '1rem', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

              {!user ? (
                <div style={{ padding: '0.5rem', color: 'var(--warning)' }}>
                  Please log in to access your projects.
                </div>
              ) : (
                <>
                  {/* Firmware selector */}
                  <div>
                    <label htmlFor="manifest-firmware" style={labelStyle}>Himax firmware version</label>
                    {isLoadingFirmwares ? (
                      <div style={{ padding: '0.5rem', color: 'var(--text-muted)' }}>Loading firmware…</div>
                    ) : (
                      <select
                        id="manifest-firmware"
                        value={himaxFirmwareId}
                        onChange={(e) => setSelectedFirmwareId(e.target.value)}
                        style={selectStyle}
                      >
                        {(firmwares || []).map((f: any) => (
                          <option key={f.id} value={f.id}>
                            {f.camera_variant ? `[${f.camera_variant}] ` : ''}{f.name} {f.is_active ? ' (Active)' : ''}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>

                  {/* Project selector */}
                  <div>
                    <label htmlFor="manifest-project" style={labelStyle}>Project</label>
                    {isLoadingProjects ? (
                      <div style={{ padding: '0.5rem', color: 'var(--text-muted)' }}>Loading projects…</div>
                    ) : projects.length > 0 ? (
                      <>
                        {searchable && (
                          <input
                            value={projectQuery}
                            onChange={e => setProjectQuery(e.target.value)}
                            placeholder={`Search ${projects.length} projects`}
                            aria-label="Search projects"
                            style={{ ...selectStyle, marginBottom: '0.375rem', boxSizing: 'border-box' }}
                          />
                        )}
                        <select
                          id="manifest-project"
                          value={selectedProjectId}
                          onChange={(e) => setChosenProjectId(e.target.value)}
                          style={selectStyle}
                        >
                          {projectOptions.map(p => (
                            <option key={p.id} value={p.id}>
                              {p.name} {p.organisation ? `(${p.organisation})` : ''}
                            </option>
                          ))}
                        </select>
                      </>
                    ) : (
                      <div style={{ padding: '0.5rem', color: 'var(--error)' }}>
                        No project you can prepare a camera for.
                      </div>
                    )}
                  </div>

                  {/* Model (read-only): name and version only; the firmware ids are in the guide */}
                  {projectModelInfo && (
                    <div style={{
                      borderTop: '1px solid var(--border)',
                      paddingTop: '1rem',
                      fontSize: '0.8125rem',
                    }}>
                      {projectModelInfo.hasModel ? (
                        projectModelInfo.incomplete ? (
                          <div style={{ color: 'var(--warning)' }}>
                            The AI model <strong>{projectModelInfo.name}</strong> has no model family or version
                            number yet, so it cannot go on a camera. Ask an organisation manager to set them.
                          </div>
                        ) : (
                          <div><strong>AI model:</strong> {projectModelInfo.name} v{projectModelInfo.version}</div>
                        )
                      ) : (
                        <div style={{ color: 'var(--text-muted)' }}>
                          This project has no AI model yet, so the package holds the camera software and settings only.
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Generate button */}
          <div>
            <button
              className="btn"
              disabled={generateMutation.isPending || !formIsValid()}
              onClick={() => generateMutation.mutate()}
              style={{ padding: '0.75rem 2rem', display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
            >
              <Package size={16} aria-hidden="true" />
              {generateMutation.isPending ? 'Submitting…' : 'Generate setup folder'}
            </button>
          </div>

          {generateMutation.isError && (
            <ErrorBanner error={{ message: (generateMutation.error as Error).message, retryable: true }} onRetry={() => generateMutation.mutate()} />
          )}
        </div>
      )}

      {/* ── Step 2: Generating ─────────────────────────────── */}
      {step === 'generating' && (
        <div style={{ maxWidth: '600px' }}>
          <div style={{
            padding: '1.5rem',
            backgroundColor: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
          }}>
            <style>{'@keyframes manifest-spin { to { transform: rotate(360deg) } }'}</style>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
              <LoaderCircle size={20} color="var(--primary)" aria-hidden="true" style={{ animation: 'manifest-spin 1s linear infinite' }} />
              <div>
                <div style={{ fontWeight: 600 }}>Generating setup folder…</div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
                  {selectedProject?.name}, {selectedFirmware?.name || 'Loading…'}
                </div>
              </div>
            </div>

            {/* Progress bar */}
            {job && (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.8125rem' }}>
                  <span>{job.message || job.status}</span>
                  <span>{Math.round(job.progress * 100)}%</span>
                </div>
                <div style={{
                  width: '100%',
                  height: '8px',
                  backgroundColor: 'var(--border)',
                  borderRadius: '4px',
                  overflow: 'hidden',
                }}>
                  <div style={{
                    width: `${job.progress * 100}%`,
                    height: '100%',
                    backgroundColor: job.status === 'failed' ? 'var(--error)' : 'var(--primary)',
                    transition: 'width 0.3s ease',
                  }} />
                </div>
              </>
            )}

            {job?.status === 'failed' && (
              <div style={{ marginTop: '1rem' }}>
                <ErrorBanner error={{ message: 'Generation failed', details: job.error, retryable: true }} onRetry={handleReset} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Step 3: Ready ──────────────────────────────────── */}
      {step === 'ready' && job && (
        <div style={{ maxWidth: '600px' }}>

          {/* Success summary card */}
          <div style={{
            padding: '1.5rem',
            backgroundColor: 'rgba(76,175,80,0.06)',
            border: '1px solid rgba(76,175,80,0.3)',
            borderRadius: 'var(--radius)',
            marginBottom: '1.5rem',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600, fontSize: '1.0625rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>
              <CircleCheck size={18} aria-hidden="true" /> Setup folder ready
            </div>

            <div style={{ fontSize: '0.8125rem', display: 'grid', gap: '0.375rem', marginBottom: '1.25rem' }}>
              <div><strong>Project:</strong> {selectedProject?.name}</div>
              {projectModelInfo?.hasModel && !projectModelInfo.incomplete && (
                <div><strong>AI Model:</strong> {projectModelInfo.name} v{projectModelInfo.version}</div>
              )}
              <div><strong>Himax Firmware:</strong> {selectedFirmware?.name}</div>
            </div>

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              {job.result_url && (
                <a
                  href={job.result_url}
                  className="btn"
                  style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.4rem', padding: '0.625rem 1.5rem' }}
                >
                  <Download size={16} aria-hidden="true" /> Download setup folder
                </a>
              )}
              <button
                onClick={handleReset}
                className="btn btn-outline"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', padding: '0.625rem 1.5rem', fontSize: '0.875rem' }}
              >
                <RefreshCw size={15} aria-hidden="true" /> Generate another
              </button>
            </div>
          </div>

          {/* Next steps: the short version; the guide has the detail */}
          <div className="card">
            <h3 style={{ marginTop: 0, marginBottom: '0.75rem', fontSize: '1rem' }}>Next steps</h3>
            <ol style={{ paddingLeft: '1.25rem', margin: 0, display: 'flex', flexDirection: 'column', gap: '0.4rem', fontSize: '0.875rem' }}>
              <li>Unzip the downloaded folder.</li>
              <li>Copy the MANIFEST folder, as it is, to the root of the SD card.</li>
              <li>Put the card in the camera and power it on.</li>
              <li>Connect to the camera from the mobile app and start monitoring.</li>
            </ol>
          </div>
        </div>
      )}
    </div>
  )
}
