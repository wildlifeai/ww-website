import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { JobProgress } from '../common'

export function GenerateManifest() {
  const [cameraType, setCameraType] = useState('Raspberry Pi')
  const [modelSource, setModelSource] = useState('default')
  const [resolution, setResolution] = useState('96')
  const [jobId, setJobId] = useState<string | null>(null)

  const generateMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/api/manifest/generate', {
        camera_type: cameraType,
        model_source: modelSource,
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

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', maxWidth: '600px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
            Camera Type
          </label>
          <select
            value={cameraType}
            onChange={(e) => setCameraType(e.target.value)}
            style={{
              width: '100%',
              padding: '0.5rem',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--surface)',
              color: 'var(--text-color)',
            }}
          >
            <option>Raspberry Pi</option>
            <option>ESP32-S3</option>
            <option>Grove Vision AI V2</option>
          </select>
        </div>

        <div>
          <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>
            Model Source
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
            <option value="default">Default (Person Detection)</option>
            <option value="sscma">SSCMA Model Zoo</option>
            <option value="organisation">Organisation Model</option>
          </select>
        </div>

        <div>
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

      <div style={{ marginTop: '1.5rem' }}>
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
