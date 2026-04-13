import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { JobProgress } from '../common'

export function GenerateManifest() {
  const [modelSource, setModelSource] = useState('Pre-trained Model')
  const [resolution, setResolution] = useState('96')
  const [modelId, setModelId] = useState<number>(1)
  const [modelVersion, setModelVersion] = useState<number>(1)
  const [jobId, setJobId] = useState<string | null>(null)

  const generateMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/api/manifest/generate', {
        model_source: modelSource,
        model_id: modelId,
        model_version: modelVersion,
        resolution,
      }),
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
    },
  })

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
            AI Model
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
          <p style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '0.25rem' }}>Select where to get the AI model from</p>
        </div>

        {/* Model Versioning */}
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1.5rem' }}>
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
                  backgroundColor: 'var(--surface)',
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
                  backgroundColor: 'var(--surface)',
                  color: 'var(--text-color)',
                }}
              />
            </div>
          </div>
          <p style={{ fontSize: '0.8125rem', opacity: 0.7, marginTop: '1rem' }}>
            Target Filename: <code>{modelId}V{modelVersion}.TFL</code>
          </p>
        </div>

        {/* Resolution */}
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1.5rem' }}>
          <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
            Resolution
          </label>
          <select
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            style={{
              width: '100%',
              padding: '0.5rem',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--surface)',
              color: 'var(--text-color)',
            }}
          >
            <option value="96">96×96</option>
            <option value="192">192×192</option>
            <option value="240">240×240</option>
          </select>
        </div>
      </div>

      <div style={{ marginTop: '1.5rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn"
          disabled={generateMutation.isPending || !!jobId}
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
