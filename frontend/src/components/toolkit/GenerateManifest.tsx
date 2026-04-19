import { useState, useEffect, useMemo } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { JobProgress } from '../common'

const PRETRAINED_MODELS: Record<string, { resolutions: string[] }> = {
  "Person Detection": { resolutions: ["96x96"] },
  "YOLOv8 Object Detection": { resolutions: ["192x192"] },
  "YOLOv11 Object Detection": { resolutions: ["192x192", "224x224"] },
  "YOLOv8 Pose Estimation": { resolutions: ["256x256"] }
}

export function GenerateManifest() {
  const { user } = useAuth()
  
  const [modelSource, setModelSource] = useState('Pre-trained Model')
  
  // Pre-trained state
  const [ptArchitecture, setPtArchitecture] = useState('Person Detection')
  const [ptResolution, setPtResolution] = useState('96x96')
  
  // SenseCap state
  const [sscmaModelId, setSscmaModelId] = useState<string>('')
  
  // Org state
  const [orgModelId, setOrgModelId] = useState<string>('')

  // Config parameters
  const [modelId, setModelId] = useState<number>(1)
  const [modelVersion, setModelVersion] = useState<number>(1)
  const [jobId, setJobId] = useState<string | null>(null)

  // Fetch SenseCap Models
  const { data: sscmaModels, isLoading: isLoadingSscma } = useQuery({
    queryKey: ['sscmaModels'],
    queryFn: async () => {
      const res = await fetch('https://raw.githubusercontent.com/Seeed-Studio/sscma-model-zoo/main/models.json')
      const json = await res.json()
      return json.models || []
    },
    enabled: modelSource === 'SenseCap Models'
  })

  // Fetch Organization Models
  const { data: orgModels, isLoading: isLoadingOrg } = useQuery({
    queryKey: ['orgModels', user?.id],
    queryFn: async () => {
      // First get user's orgs
      // Simplified: just get all ai_models where user's org matches.
      // In a full implementation, you'd fetch orgs first, then models.
      // For now, we fetch 'ai_models' where deleted_at is null
      const { data, error } = await supabase
        .from('ai_models')
        .select('*')
        .is('deleted_at', null)
      if (error) throw error
      return data || []
    },
    enabled: modelSource === 'My Organization Models' && !!user
  })

  // Automatically update resolution based on architecture
  useEffect(() => {
    if (modelSource === 'Pre-trained Model') {
      const availableRes = PRETRAINED_MODELS[ptArchitecture]?.resolutions || []
      if (!availableRes.includes(ptResolution) && availableRes.length > 0) {
        setPtResolution(availableRes[0])
      }
    }
  }, [ptArchitecture, modelSource, ptResolution])

  // Figure out the active resolution based on the selections
  const computedResolution = useMemo(() => {
    if (modelSource === 'Pre-trained Model') return ptResolution
    
    if (modelSource === 'SenseCap Models' && sscmaModelId && sscmaModels) {
      const model = (sscmaModels as any[]).find(m => m.uuid === sscmaModelId || m.name === sscmaModelId)
      if (model) {
        try {
          const shape = model.network?.input?.shape || []
          if (shape.length >= 2) return `${shape[0]}x${shape[1]}`
        } catch (e) {}
      }
    }
    
    // For org models, whatever the model implies, we usually store it or return default
    return "Unknown"
  }, [modelSource, ptResolution, sscmaModelId, sscmaModels])

  const generateMutation = useMutation({
    mutationFn: () => {
      // Build final payload
      let finalModelName = 'None'
      
      if (modelSource === 'Pre-trained Model') {
        finalModelName = `${ptArchitecture} (${ptResolution})`
      } else if (modelSource === 'SenseCap Models') {
        finalModelName = sscmaModelId
      } else if (modelSource === 'My Organization Models') {
        const found = orgModels?.find((m: any) => m.id === orgModelId)
        finalModelName = found ? `${found.name} v${found.version}` : 'Unknown'
      }

      return apiClient.post('/api/manifest/generate', {
        model_source: modelSource,
        model_name: finalModelName,
        model_id: modelId,
        model_version: modelVersion,
        resolution: computedResolution,
      })
    },
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
    },
  })

  const formIsValid = () => {
    if (modelSource === 'SenseCap Models' && !sscmaModelId) return false
    if (modelSource === 'My Organization Models' && (!orgModels || orgModels.length === 0 || !orgModelId)) return false
    return true
  }

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Generate Firmware Manifest</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Build a <code>MANIFEST.zip</code> containing firmware binaries, model files,
        and configuration for your Wildlife Watcher device.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '1.5rem', maxWidth: '600px' }}>
        
        {/* Model Source */}
        <div>
          <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
            AI Model Source
          </label>
          <select
            value={modelSource}
            onChange={(e) => setModelSource(e.target.value)}
            style={{
              width: '100%',
              padding: '0.5rem',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--surface)',
              color: 'var(--text-color)',
            }}
          >
            <option value="Pre-trained Model">Pre-trained Model</option>
            <option value="SenseCap Models">SenseCap Models</option>
            <option value="My Organization Models">My Organization Models</option>
            <option value="No Model">No Model</option>
          </select>
        </div>

        {/* Dynamic Model Sub-selection */}
        {modelSource !== 'No Model' && (
          <div style={{ padding: '1rem', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
            
            {modelSource === 'Pre-trained Model' && (
              <div style={{ display: 'flex', gap: '1rem' }}>
                <div style={{ flex: 2 }}>
                  <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>Model Architecture</label>
                  <select
                    value={ptArchitecture}
                    onChange={(e) => setPtArchitecture(e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}
                  >
                    {Object.keys(PRETRAINED_MODELS).map(k => <option key={k} value={k}>{k}</option>)}
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>Resolution</label>
                  <select
                    value={ptResolution}
                    onChange={(e) => setPtResolution(e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}
                  >
                    {PRETRAINED_MODELS[ptArchitecture]?.resolutions.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
              </div>
            )}

            {modelSource === 'SenseCap Models' && (
              <div>
                <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>SenseCap Zoo Model</label>
                {isLoadingSscma ? (
                  <div style={{ padding: '0.5rem', opacity: 0.6 }}>Loading models…</div>
                ) : (
                  <select
                    value={sscmaModelId}
                    onChange={(e) => setSscmaModelId(e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}
                  >
                    <option value="">Select a SenseCap Model…</option>
                    {(sscmaModels as any[])?.map(m => (
                      <option key={m.uuid || m.name} value={m.name}>{m.name}</option>
                    ))}
                  </select>
                )}
                {computedResolution !== "Unknown" && <p style={{ fontSize: '0.75rem', opacity: 0.8, marginTop: '0.5rem' }}>Resolution: {computedResolution}</p>}
              </div>
            )}

            {modelSource === 'My Organization Models' && (
              <div>
                <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>Organization Model</label>
                {isLoadingOrg ? (
                  <div style={{ padding: '0.5rem', opacity: 0.6 }}>Loading models…</div>
                ) : orgModels && orgModels.length > 0 ? (
                  <select
                    value={orgModelId}
                    onChange={(e) => setOrgModelId(e.target.value)}
                    style={{ width: '100%', padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}
                  >
                    <option value="">Select an Org Model…</option>
                    {orgModels?.map((m: any) => (
                      <option key={m.id} value={m.id}>{m.name} (v{m.version})</option>
                    ))}
                  </select>
                ) : (
                  <div style={{ padding: '0.5rem', color: 'var(--error)' }}>No models found or access denied.</div>
                )}
              </div>
            )}

            {/* Model Versioning Moved Inside Conditional */}
            <div style={{ borderTop: '1px solid var(--border)', marginTop: '1.5rem', paddingTop: '1.5rem' }}>
              <h4 style={{ marginBottom: '0.5rem' }}>🔢 Model Versioning</h4>
              <p style={{ fontSize: '0.8125rem', opacity: 0.8, marginBottom: '1rem' }}>
                Define the Model ID and Version matching your model. The firmware will use these to load the correct file.
              </p>
              <div style={{ display: 'flex', gap: '1rem' }}>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
                    Model ID (OP 14)
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={modelId}
                    onChange={(e) => setModelId(Number(e.target.value) || 1)}
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      borderRadius: 'var(--radius)',
                      border: '1px solid var(--border)',
                      backgroundColor: 'transparent',
                      color: 'var(--text-color)',
                    }}
                  />
                </div>
                
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
                    Version (OP 15)
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={modelVersion}
                    onChange={(e) => setModelVersion(Number(e.target.value) || 1)}
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      borderRadius: 'var(--radius)',
                      border: '1px solid var(--border)',
                      backgroundColor: 'transparent',
                      color: 'var(--text-color)',
                    }}
                  />
                </div>
              </div>
              <p style={{ fontSize: '0.8125rem', opacity: 0.7, marginTop: '1rem' }}>
                Target Filename: <code>{modelId}V{modelVersion}.TFL</code>
              </p>
            </div>

          </div>
        )}
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
